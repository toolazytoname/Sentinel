"""Static macro-event calendar → veto Rule 3 ("major event window").

`app/modules/veto.py::check_rules` VETOes when a proposed trade sits inside a
`0 < upcoming_event_window_minutes < 30` window. This module supplies that
value from a hand-maintained JSON calendar of major macro events (FOMC, CPI,
NFP, ...) so the otherwise-inert Rule 3 actually fires ahead of known shocks.

Design goals:
  - Pure, testable functions: `minutes_until_next_event` takes `now` explicitly
    so tests never need to freeze the clock.
  - Fail-open (ADR-002): a missing / malformed / non-list calendar degrades to
    "no imminent event" (0) and never raises into the /veto request path.

The bundled data file lives at ``app/data/event_calendar.json``; override the
path with the ``EVENT_CALENDAR_PATH`` env var.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Bundled, hand-maintained calendar shipped with the service.
DEFAULT_CALENDAR_PATH = Path(__file__).resolve().parent.parent / "data" / "event_calendar.json"

# Only report events starting within this horizon; beyond it we return 0
# ("no imminent event"). Rule 3 itself only fires under 30 min, but reporting
# up to the horizon keeps the value meaningful for logging/observability.
IMMINENT_HORIZON_MINUTES = 60


def _resolve_path(path: str | None) -> str:
    """Pick the calendar path: explicit arg > EVENT_CALENDAR_PATH env > bundled."""
    return path or os.environ.get("EVENT_CALENDAR_PATH") or str(DEFAULT_CALENDAR_PATH)


def _parse_iso_utc(value: object) -> datetime | None:
    """Parse an ISO8601 timestamp to a tz-aware UTC datetime, or None on junk.

    Accepts a trailing ``Z`` (treated as +00:00) and naive timestamps (assumed
    UTC). Never raises — malformed input yields None so the caller can skip it.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_event_calendar(path: str | None = None) -> list[dict]:
    """Read the event calendar JSON. Returns [] on any missing/malformed file.

    Never raises — a broken calendar must not break the veto path. Only dict
    entries survive; anything else in the list is dropped.
    """
    resolved = _resolve_path(path)
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("event calendar load failed at %s: %s; using empty calendar", resolved, exc)
        return []

    if not isinstance(data, list):
        logger.warning("event calendar at %s is not a JSON list; using empty calendar", resolved)
        return []

    return [entry for entry in data if isinstance(entry, dict)]


def minutes_until_next_event(now: datetime, calendar: list[dict]) -> int:
    """Minutes from ``now`` until the nearest imminent event start.

    Semantics (see Rule 3 in veto.py, which fires on ``0 < x < 30``):
      - If ``now`` is INSIDE any event window (start <= now <= end) → return 1.
      - Else return the whole minutes until the nearest UPCOMING start, when
        that start is within ``IMMINENT_HORIZON_MINUTES``.
      - Else (no event within the horizon) → return 0 (no imminent event).

    Pure function: robust to malformed entries (they are skipped). All
    timestamps are treated as UTC.
    """
    nearest_delta_min: float | None = None

    for event in calendar:
        start = _parse_iso_utc(event.get("start"))
        if start is None:
            continue

        end = _parse_iso_utc(event.get("end"))
        # Currently inside a live event window → most imminent possible.
        if end is not None and start <= now <= end:
            return 1

        if start > now:
            delta_min = (start - now).total_seconds() / 60.0
            if nearest_delta_min is None or delta_min < nearest_delta_min:
                nearest_delta_min = delta_min

    if nearest_delta_min is None or nearest_delta_min > IMMINENT_HORIZON_MINUTES:
        return 0
    # Guard the sub-minute case so an event 20s out still trips Rule 3.
    return max(1, round(nearest_delta_min))


def current_event_window_minutes(now: datetime | None = None) -> int:
    """Fail-open convenience wrapper for the two veto call sites.

    Loads the calendar and computes the window in one call. ANY exception
    degrades to 0 (no veto) so a broken calendar can never 500 /veto.
    """
    try:
        moment = now or datetime.now(timezone.utc)
        return minutes_until_next_event(moment, load_event_calendar())
    except Exception:  # noqa: BLE001 - a broken calendar must never break /veto
        logger.warning("event window computation failed; failing open to 0", exc_info=True)
        return 0
