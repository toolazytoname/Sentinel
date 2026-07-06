"""Tests for the research ingester.

Mocks both the HTTP source (CoinGecko) and the LLM extractor so we can
verify orchestration logic without network or API key.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.llm import LLMUnavailable, ResearchExtractor
from app.llm.client import StructuredExtractor
from app.modules.research import (
    CoinGeckoEventsSource,
    EventSource,
    ResearchIngester,
    _normalize_coingecko_event,
)
from app.schemas import ResearchNote


# --- Test doubles ---

class FakeSource(EventSource):
    def __init__(self, events: list[dict[str, Any]]):
        self._events = events
        self.calls = 0

    def fetch_raw_events(self) -> list[dict[str, Any]]:
        self.calls += 1
        return self._events


class FakeResearchExtractor:
    """Mirrors ResearchExtractor's interface but bypasses LLM."""

    def __init__(self, notes: list[ResearchNote], raise_after: int | None = None):
        self._notes = list(notes)
        self._raise_after = raise_after
        self.calls = 0

    def extract(self, prompt: str) -> ResearchNote:
        self.calls += 1
        if self._raise_after is not None and self.calls > self._raise_after:
            raise LLMUnavailable("simulated outage")
        if not self._notes:
            raise AssertionError("FakeResearchExtractor exhausted")
        return self._notes.pop(0)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    yield SessionLocal
    engine.dispose()


def _make_note(asset: str = "BTC", severity: int = 3) -> ResearchNote:
    return ResearchNote(
        asset=asset,
        event_type="regulatory",
        severity=severity,
        summary=f"Test summary for {asset} at severity {severity}",
        source_url=f"https://example.com/{asset.lower()}",
        published_at="2026-07-06T08:00:00Z",
    )


# --- Tests ---

class TestNormalizeEvent:
    def test_normalizes_coingecko_event(self):
        raw = {
            "title": "BTC ETF update",
            "description": "SEC delays decision",
            "link": "https://example.com/news",
            "date": "2026-07-06",
        }
        out = _normalize_coingecko_event("bitcoin", raw)
        assert out["asset"] == "BITCOIN"
        assert out["title"] == "BTC ETF update"
        assert out["url"] == "https://example.com/news"
        assert out["published_at"] == "2026-07-06"

    def test_handles_missing_fields_with_defaults(self):
        out = _normalize_coingecko_event("ethereum", {})
        assert out["asset"] == "ETHEREUM"
        assert out["title"] == ""
        assert out["url"] == "https://www.coingecko.com"  # fallback


class TestIngesterOrchestration:
    def test_persists_all_events_when_llm_works(self, session_factory):
        events = [
            {"asset": "BTC", "title": "A", "url": "https://x.com/a", "published_at": "2026-07-06"},
            {"asset": "ETH", "title": "B", "url": "https://x.com/b", "published_at": "2026-07-06"},
        ]
        notes = [_make_note("BTC", 4), _make_note("ETH", 2)]
        extractor = FakeResearchExtractor(notes)
        source = FakeSource(events)
        ingester = ResearchIngester(source, extractor, session_factory)
        persisted = ingester.run_once()
        assert persisted == 2
        assert extractor.calls == 2

    def test_skips_event_when_llm_unavailable_continues_others(self, session_factory):
        events = [
            {"asset": "BTC", "title": "A", "url": "https://x.com/a", "published_at": "2026-07-06"},
            {"asset": "ETH", "title": "B", "url": "https://x.com/b", "published_at": "2026-07-06"},
            {"asset": "SOL", "title": "C", "url": "https://x.com/c", "published_at": "2026-07-06"},
        ]
        # First call succeeds, then LLM goes down
        extractor = FakeResearchExtractor(
            [_make_note("BTC", 4)],
            raise_after=1,
        )
        source = FakeSource(events)
        ingester = ResearchIngester(source, extractor, session_factory)
        persisted = ingester.run_once()
        assert persisted == 1, "Should persist only the first; LLM was down for rest"

    def test_empty_events_returns_zero(self, session_factory):
        extractor = FakeResearchExtractor([])
        source = FakeSource([])
        ingester = ResearchIngester(source, extractor, session_factory)
        assert ingester.run_once() == 0

    def test_persisted_data_round_trips_through_db(self, session_factory):
        note = _make_note("BTC", 5)
        extractor = FakeResearchExtractor([note])
        source = FakeSource(
            [{"asset": "BTC", "title": "ETF", "url": "https://x.com/etf", "published_at": "2026-07-06"}]
        )
        ingester = ResearchIngester(source, extractor, session_factory)
        ingester.run_once()
        # Verify by querying DB directly
        from app.db import recent_high_severity_assets
        with session_factory() as s:
            assets = recent_high_severity_assets(s, since_hours=1)
            assert "BTC" in assets


class TestCoinGeckoSourceIntegration:
    """Verifies the real CoinGecko source class can be instantiated and uses correct URL.

    Does NOT make a real HTTP call — that's left to integration/manual testing.
    """
    def test_uses_correct_endpoints(self):
        captured_urls: list[str] = []

        def responder(request: httpx.Request) -> httpx.Response:
            captured_urls.append(str(request.url))
            return httpx.Response(200, json={"data": []})

        source = CoinGeckoEventsSource()
        source._client = httpx.Client(transport=httpx.MockTransport(responder))
        out = source.fetch_raw_events()
        assert out == []
        # Both tracked coins should be queried (order independent)
        assert len(captured_urls) == 2
        assert any("/coins/bitcoin/events" in u for u in captured_urls)
        assert any("/coins/ethereum/events" in u for u in captured_urls)
        source.close()