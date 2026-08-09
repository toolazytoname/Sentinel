"""Static safety contract for the deployed dry-run runtime.

These tests deliberately inspect the files mounted into the freqtrade
container.  A template is not a runtime guarantee: the dry-run JSON and the
deployed strategy must independently retain Sentinel's safety controls.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DRY_RUN_CONFIG = REPO_ROOT / "deploy/user_data/config/dry-run.json"
DEPLOYED_S1 = REPO_ROOT / "deploy/user_data/strategies/S1TrendFollow.py"
SOURCE_S1 = REPO_ROOT / "strategies/s1_trend_follow/strategy.py"
START_SCRIPT = REPO_ROOT / "deploy/start.sh"


def _config() -> dict:
    return json.loads(DRY_RUN_CONFIG.read_text(encoding="utf-8"))


def _strategy_methods(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    strategy = next(
        node for node in ast.walk(module) if isinstance(node, ast.ClassDef) and node.name == "S1TrendFollow"
    )
    return {node.name for node in strategy.body if isinstance(node, ast.FunctionDef)}


def test_dry_run_starts_and_binds_frequi_to_loopback():
    config = _config()
    assert config["dry_run"] is True
    assert config["initial_state"] == "running"
    assert config["api_server"]["listen_ip_address"] == "127.0.0.1"


def test_dry_run_keeps_core_risk_limits_enabled():
    config = _config()
    assert config["max_open_trades"] <= 3
    assert config["stoploss_on_exchange"] is True
    assert config["telegram"]["enabled"] is True
    assert "protections" not in config
    assert "protections" in _strategy_methods(DEPLOYED_S1)


def test_s1_protection_contract_is_kept_in_both_strategy_copies():
    assert "protections" in _strategy_methods(SOURCE_S1)
    assert "protections" in _strategy_methods(DEPLOYED_S1)


def test_start_script_accepts_legacy_telegram_environment_names():
    script = START_SCRIPT.read_text(encoding="utf-8")
    assert "FREQTRADE__TELEGRAM__TOKEN" in script
    assert "FREQTELEGRAM__TELEGRAM_TOKEN" in script
