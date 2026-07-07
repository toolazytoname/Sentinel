"""Tests for the static event-calendar loader (veto Rule 3 wiring).

Covers the pure functions in app.modules.events:
  - load_event_calendar: missing / malformed / valid → never crashes
  - minutes_until_next_event: upcoming / far-away / inside-window / empty
  - the fail-open wrapper current_event_window_minutes

Plus a proof that the derived window actually fires check_rules' Rule 3.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.events import (
    current_event_window_minutes,
    load_event_calendar,
    minutes_until_next_event,
)
from app.modules.veto import MarketContext, TradeSignal, check_rules


NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _event(name: str, start: datetime, end: datetime) -> dict:
    return {
        "name": name,
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _signal() -> TradeSignal:
    return TradeSignal(strategy="S1", pair="BTC/USDT", side="long", stake_pct=0.05)


def _context(minutes: int) -> MarketContext:
    return MarketContext(
        recent_high_severity_events=[],
        current_total_exposure_pct=0.10,
        upcoming_event_window_minutes=minutes,
    )


# --- minutes_until_next_event ---

class TestMinutesUntilNextEvent:
    def test_event_20_min_ahead_returns_about_20_and_vetoes(self):
        cal = [_event("CPI", NOW + timedelta(minutes=20), NOW + timedelta(minutes=50))]
        minutes = minutes_until_next_event(NOW, cal)
        assert 19 <= minutes <= 21

        # The derived window must actually trip Rule 3 (0 < x < 30).
        vetoed, reason = check_rules(_signal(), _context(minutes))
        assert vetoed is True
        assert "event_window" in reason

    def test_event_2_hours_ahead_returns_zero_no_veto(self):
        cal = [_event("FOMC", NOW + timedelta(hours=2), NOW + timedelta(hours=3))]
        minutes = minutes_until_next_event(NOW, cal)
        assert minutes == 0

        vetoed, _ = check_rules(_signal(), _context(minutes))
        assert vetoed is False

    def test_now_inside_window_returns_one_and_vetoes(self):
        cal = [_event("FOMC", NOW - timedelta(minutes=5), NOW + timedelta(minutes=25))]
        minutes = minutes_until_next_event(NOW, cal)
        assert minutes == 1

        vetoed, reason = check_rules(_signal(), _context(minutes))
        assert vetoed is True
        assert "event_window" in reason

    def test_empty_calendar_returns_zero(self):
        assert minutes_until_next_event(NOW, []) == 0

    def test_past_event_only_returns_zero(self):
        cal = [_event("old", NOW - timedelta(hours=3), NOW - timedelta(hours=2))]
        assert minutes_until_next_event(NOW, cal) == 0

    def test_returns_nearest_of_several(self):
        cal = [
            _event("far", NOW + timedelta(minutes=45), NOW + timedelta(minutes=60)),
            _event("near", NOW + timedelta(minutes=10), NOW + timedelta(minutes=40)),
        ]
        minutes = minutes_until_next_event(NOW, cal)
        assert 9 <= minutes <= 11

    def test_malformed_entries_are_skipped_not_crash(self):
        cal = [
            {"name": "no-start"},
            {"name": "bad-start", "start": "not-a-date", "end": "also-bad"},
            _event("good", NOW + timedelta(minutes=15), NOW + timedelta(minutes=45)),
        ]
        minutes = minutes_until_next_event(NOW, cal)
        assert 14 <= minutes <= 16


# --- load_event_calendar ---

class TestLoadEventCalendar:
    def test_valid_file(self, tmp_path):
        p = tmp_path / "cal.json"
        p.write_text(json.dumps([_event("CPI", NOW, NOW + timedelta(minutes=30))]))
        cal = load_event_calendar(str(p))
        assert isinstance(cal, list)
        assert cal[0]["name"] == "CPI"

    def test_missing_file_returns_empty(self, tmp_path):
        cal = load_event_calendar(str(tmp_path / "does-not-exist.json"))
        assert cal == []

    def test_malformed_json_returns_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{ not valid json ]")
        assert load_event_calendar(str(p)) == []

    def test_non_list_json_returns_empty(self, tmp_path):
        p = tmp_path / "obj.json"
        p.write_text(json.dumps({"name": "not a list"}))
        assert load_event_calendar(str(p)) == []

    def test_env_override_is_honored(self, tmp_path, monkeypatch):
        p = tmp_path / "env-cal.json"
        p.write_text(json.dumps([_event("ENV", NOW, NOW + timedelta(minutes=10))]))
        monkeypatch.setenv("EVENT_CALENDAR_PATH", str(p))
        cal = load_event_calendar()
        assert cal[0]["name"] == "ENV"

    def test_bundled_default_file_loads(self):
        """The shipped data file must exist and parse as a list of dicts."""
        cal = load_event_calendar()
        assert isinstance(cal, list)
        assert all(isinstance(e, dict) for e in cal)


# --- current_event_window_minutes (fail-open wrapper) ---

class TestCurrentEventWindowMinutes:
    def test_never_raises_on_broken_calendar(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVENT_CALENDAR_PATH", str(tmp_path / "missing.json"))
        assert current_event_window_minutes() == 0

    def test_reflects_imminent_event(self, tmp_path, monkeypatch):
        p = tmp_path / "cal.json"
        start = datetime.now(timezone.utc) + timedelta(minutes=15)
        end = start + timedelta(minutes=30)
        p.write_text(json.dumps([_event("imminent", start, end)]))
        monkeypatch.setenv("EVENT_CALENDAR_PATH", str(p))
        minutes = current_event_window_minutes()
        assert 13 <= minutes <= 16
