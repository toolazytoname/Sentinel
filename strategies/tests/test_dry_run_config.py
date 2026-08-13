"""Config contracts for S1/S2 dry-run (iron rules + user 2026-08-13 拍板)."""
from __future__ import annotations

import json
from pathlib import Path

from strategies.universe import MAX_OPEN_TRADES_CAP, USDT_SPOT_UNIVERSE

_REPO = Path(__file__).resolve().parents[2]
_CONFIG = _REPO / "deploy" / "user_data" / "config"
_STRAT = _REPO / "deploy" / "user_data" / "strategies"
_COMPOSE = _REPO / "deploy" / "docker-compose.yml"


def _load(name: str) -> dict:
    return json.loads((_CONFIG / name).read_text(encoding="utf-8"))


def test_s1_dry_run_starts_running_with_iron_rules():
    cfg = _load("dry-run.json")
    assert cfg["initial_state"] == "running"
    assert cfg["dry_run"] is True
    assert cfg["max_open_trades"] <= MAX_OPEN_TRADES_CAP
    assert cfg["stoploss_on_exchange"] is True
    assert cfg["strategy"] == "S1TrendFollow"
    assert cfg["timeframe"] == "1d"
    assert tuple(cfg["exchange"]["pair_whitelist"]) == USDT_SPOT_UNIVERSE


def test_s2_dry_run_is_separate_bot():
    cfg = _load("dry-run-s2.json")
    assert cfg["initial_state"] == "running"
    assert cfg["dry_run"] is True
    assert cfg["max_open_trades"] <= MAX_OPEN_TRADES_CAP
    assert cfg["stoploss_on_exchange"] is True
    assert cfg["strategy"] == "S2MomentumRotation"
    assert cfg["api_server"]["listen_port"] == 8081
    assert "s2" in cfg["db_url"]
    assert cfg["webhook"]["exit_fill"]["strategy"] == "S2MomentumRotation"
    assert tuple(cfg["exchange"]["pair_whitelist"]) == USDT_SPOT_UNIVERSE


def test_s2_strategy_file_exists_and_keeps_protections():
    text = (_STRAT / "S2MomentumRotation.py").read_text(encoding="utf-8")
    assert "class S2MomentumRotation" in text
    assert "MaxDrawdown" in text
    assert "StoplossGuard" in text
    assert "CooldownPeriod" in text
    assert "stoploss_on_exchange = True" in text


def test_s1_strategy_has_pullback_and_custom_stoploss_flag():
    text = (_STRAT / "S1TrendFollow.py").read_text(encoding="utf-8")
    assert "pullback_reclaim" in text
    assert "use_custom_stoploss = True" in text
    assert "MaxDrawdown" in text


def test_compose_has_s2_service_on_8081():
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "sentinel-freqtrade-s2" in text
    assert "S2MomentumRotation" in text
    assert "8081" in text
    assert "initial_state" not in text  # lives in json, not compose
