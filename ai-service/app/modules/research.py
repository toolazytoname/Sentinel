"""Research module: ingest market events, summarize via LLM, persist to DB.

Flow per docs/system/02-design.md §2.2 (Research Module):
  1. Scheduled job fetches raw events from sources (CoinGecko initially)
  2. For each event, build a prompt + call LLM via ResearchExtractor
  3. Persist structured ResearchNoteRow to DB
  4. severity>=4 → enqueue for Telegram (out of scope for this PR)

We intentionally keep this thin: the heavy lifting (LLM extraction, DB,
HTTP retry) lives in other modules. This module just orchestrates.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.db import insert_research_note
from app.llm import LLMUnavailable, ResearchExtractor
from app.schemas import ResearchNote

logger = logging.getLogger(__name__)


# --- Source adapters ---

class EventSource(ABC):
    """Abstract base for event sources. Implemented by each data provider."""

    @abstractmethod
    def fetch_raw_events(self) -> list[dict[str, Any]]:
        """Return list of raw event dicts. Each dict has at minimum:
        asset, title, url, published_at.
        """
        raise NotImplementedError


class CoinGeckoEventsSource:
    """CoinGecko /coins/{id}/events endpoint — free tier, no API key needed."""

    COINGECKO_BASE = "https://api.coingecko.com/api/v3"

    # Top coins to monitor. Expand over time; YAGNI for now.
    TRACKED_COINS = ("bitcoin", "ethereum")

    def __init__(self, https_proxy: str | None = None, timeout: float = 20.0):
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "Sentinel/0.1 (research-ingest)"},
            proxy=https_proxy,
        )

    def close(self) -> None:
        self._client.close()

    def fetch_raw_events(self) -> list[dict[str, Any]]:
        """Fetch recent events for tracked coins. Returns normalized dicts."""
        out: list[dict[str, Any]] = []
        for coin in self.TRACKED_COINS:
            url = f"{self.COINGECKO_BASE}/coins/{coin}/events"
            try:
                resp = self._client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("CoinGecko fetch failed for %s: %s", coin, e)
                continue
            for ev in data.get("data", []):
                out.append(_normalize_coingecko_event(coin, ev))
        return out


def _normalize_coingecko_event(coin_id: str, ev: dict[str, Any]) -> dict[str, Any]:
    """Map a CoinGecko event dict to our internal shape."""
    return {
        "asset": coin_id.upper(),  # "bitcoin" → "BITCOIN"
        "title": ev.get("title", ""),
        "description": ev.get("description", ""),
        "url": ev.get("link", "https://www.coingecko.com"),
        "published_at": ev.get("date", ""),
        # CoinGecko doesn't expose severity directly; default to 3 (medium).
        # LLM will re-rate it during extraction.
        "raw_severity_hint": 3,
    }


# --- Orchestrator ---

class ResearchIngester:
    """Pulls from sources, summarizes via LLM, persists to DB."""

    def __init__(
        self,
        source: EventSource,
        extractor: ResearchExtractor,
        session_factory,
    ):
        self._source = source
        self._extractor = extractor
        self._session_factory = session_factory

    def run_once(self) -> int:
        """One ingestion pass. Returns count of notes successfully persisted."""
        raw_events = self._source.fetch_raw_events()
        if not raw_events:
            logger.info("No raw events to process")
            return 0

        persisted = 0
        for raw in raw_events:
            try:
                note = self._extract(raw)
                self._persist(note)
                persisted += 1
            except LLMUnavailable as e:
                logger.warning("Skipping event due to LLM unavailable: %s", e)
                continue
            except Exception as e:  # don't let one bad event kill the whole batch
                logger.exception("Failed to process event: %s", e)
                continue
        return persisted

    def _extract(self, raw: dict[str, Any]) -> ResearchNote:
        prompt = _build_extraction_prompt(raw)
        return self._extractor.extract(prompt)

    def _persist(self, note: ResearchNote) -> None:
        with self._session_factory() as session:
            insert_research_note(
                session,
                asset=note.asset,
                event_type=note.event_type,
                severity=note.severity,
                summary=note.summary,
                source_url=note.source_url,
                published_at=note.published_at,
            )


def _build_extraction_prompt(raw: dict[str, Any]) -> str:
    return (
        f"You are extracting structured market intelligence from a raw news item.\n\n"
        f"Asset: {raw.get('asset', 'UNKNOWN')}\n"
        f"Title: {raw.get('title', '(no title)')}\n"
        f"Description: {raw.get('description', '')[:500]}\n"
        f"Source URL: {raw.get('url', '')}\n"
        f"Published at: {raw.get('published_at', '')}\n\n"
        f"Rate the severity 1-5 (1=trivial, 5=critical market-moving) and choose "
        f"an event_type from: regulatory, technical, partnership, macro, security, other.\n"
        f"Output a single JSON object matching the schema."
    )