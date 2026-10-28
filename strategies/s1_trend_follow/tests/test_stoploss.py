"""R4.1: pin absolute stop prices and execute the real Freqtrade adapter."""
from __future__ import annotations

import ast
import io
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from strategies.s1_trend_follow import strategy as source


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy/user_data/strategies/S1TrendFollow.py"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_trailing_waits_until_price_has_risen_above_entry():
    row = pd.Series({"close": 94.0, "adx": 30.0})
    assert source.exit_signal(row, 100.0, 100.0, source.StrategyParams()) == (False, "hold")


@pytest.mark.parametrize(
    "entry,peak,hard,trail,expected",
    [
        (100, 100, 0.08, 0.05, 92),
        (100, 99, 0.08, 0.05, 92),
        (100, 101, 0.08, 0.05, 95.95),
        (100, 120, 0.08, 0.05, 114),
        (100, 120, 0.05, 0.10, 108),
        (100, 100, 0.15, 0.05, 90),
    ],
)
def test_absolute_stop_target(entry, peak, hard, trail, expected):
    assert source.long_stop_price(entry, peak, hard, trail) == pytest.approx(expected)


def test_pure_logic_keeps_the_engine_ten_percent_floor():
    row = pd.Series({"close": 89.0, "adx": 30.0})
    params = source.StrategyParams(hard_stop_pct=0.15)
    assert source.exit_signal(row, 100.0, 100.0, params) == (True, "hard_stop")


@pytest.mark.parametrize("path", [Path(source.__file__), DEPLOY], ids=["source", "deploy"])
def test_custom_stoploss_is_explicitly_enabled(path):
    tree = ast.parse(path.read_text())
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "S1TrendFollow")
    assert any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "use_custom_stoploss" for t in n.targets)
        and isinstance(n.value, ast.Constant) and n.value.value is True
        for n in cls.body
    )


@pytest.fixture(params=["source", "deploy"])
def runtime_strategy(request, monkeypatch, tmp_path):
    pytest.importorskip("freqtrade", reason="real Freqtrade runtime required")
    from freqtrade.enums import TradingMode
    from freqtrade.resolvers import StrategyResolver
    from freqtrade.strategy import IStrategy

    path = Path(source.__file__) if request.param == "source" else DEPLOY
    if request.param == "deploy":
        monkeypatch.syspath_prepend(str(DEPLOY.parent))
    config = {
        "dry_run": True, "trading_mode": TradingMode.SPOT, "stake_currency": "USDT",
        "strategy": "S1TrendFollow", "strategy_path": str(path.parent),
        "user_data_dir": tmp_path,
    }
    instance = StrategyResolver.load_strategy(config)
    assert isinstance(instance, IStrategy)
    assert Path(instance.__file__).resolve() == path.resolve()
    instance.hard_stop = deepcopy(instance.hard_stop)
    trail_name = "trailing_stop_pct" if request.param == "source" else "trailing_stop_p"
    setattr(instance, trail_name, deepcopy(getattr(instance, trail_name)))
    return instance


def _trade():
    from freqtrade.persistence import LocalTrade

    return LocalTrade(
        pair="BTC/USDT", open_rate=100.0, amount=1.0, stake_amount=100.0,
        open_date=NOW, fee_open=0.001, fee_close=0.001, is_short=False,
        leverage=1.0, max_rate=100.0,
    )


def test_real_engine_anchors_fixed_stop_to_entry(runtime_strategy):
    strategy = runtime_strategy
    trade = _trade()
    assert strategy.use_custom_stoploss is True
    assert strategy.stoploss == -0.10
    for rate in [100.0, 97.0, 94.0]:
        strategy.ft_stoploss_adjust(rate, trade, NOW, trade.calc_profit_ratio(rate), 0)
        assert trade.stop_loss == pytest.approx(92.0)


def test_real_engine_arms_on_fill_and_never_widens_on_refresh(runtime_strategy):
    strategy = runtime_strategy
    trade = _trade()
    strategy.ft_stoploss_adjust(100.0, trade, NOW, 0.0, 0, after_fill=True)
    assert trade.stop_loss == pytest.approx(92.0)
    trade.adjust_stop_loss(120.0, 0.05)
    trade.max_rate = 120.0
    getattr(strategy, "trailing_stop_p", getattr(strategy, "trailing_stop_pct", None)).value = 0.10
    strategy.ft_stoploss_adjust(118.0, trade, NOW, 0.18, 0, after_fill=True)
    assert trade.stop_loss == pytest.approx(114.0)


def test_real_engine_ratchets_peak_and_exits_on_retracement(runtime_strategy):
    strategy = runtime_strategy
    trade = _trade()
    for rate, expected in [(100.0, 92.0), (110.0, 104.5), (120.0, 114.0), (116.0, 114.0)]:
        trade.adjust_min_max_rates(rate, rate)
        strategy.ft_stoploss_adjust(rate, trade, NOW, trade.calc_profit_ratio(rate), 0)
        assert trade.stop_loss == pytest.approx(expected)
    exit_check = strategy.ft_stoploss_reached(113.0, trade, NOW, trade.calc_profit_ratio(113.0), 0)
    assert exit_check.exit_flag is True
    assert trade.stop_loss == pytest.approx(114.0)


@pytest.mark.parametrize("hard,trail", [(0.05, 0.10), (0.15, 0.05)])
def test_real_callback_matches_pure_stop_target(runtime_strategy, hard, trail):
    strategy = runtime_strategy
    strategy.hard_stop.value = hard
    getattr(strategy, "trailing_stop_p", getattr(strategy, "trailing_stop_pct", None)).value = trail
    trade = _trade()
    trade.max_rate = 120.0
    ratio = strategy.custom_stoploss("BTC/USDT", trade, NOW, 118.0, -0.001)
    target = source.long_stop_price(100.0, 120.0, hard, trail)
    assert 118.0 * (1 - abs(ratio)) == pytest.approx(target)


@pytest.mark.parametrize("decision,expected", [("VETO", False), ("PASS", True), ("timeout", True)])
@pytest.mark.parametrize("mode", [None, "dry_run", "live"])
def test_real_strategy_preserves_ai_veto_and_fail_open(runtime_strategy, monkeypatch, decision, expected, mode):
    from unittest.mock import MagicMock
    from freqtrade.enums import RunMode

    if mode is not None:
        runtime_strategy.config["runmode"] = RunMode(mode)
    if decision == "timeout":
        def urlopen(*args, **kwargs):
            raise TimeoutError("offline test")
    else:
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps({"decision": decision}).encode())

        def urlopen(*args, **kwargs):
            return response

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert runtime_strategy.confirm_trade_entry(
        pair="BTC/USDT", order_type="limit", amount=1.0, rate=100.0,
        time_in_force="GTC", current_time=NOW, entry_tag=None, side="long",
    ) is expected


def test_real_strategy_retains_all_protections(runtime_strategy):
    assert {item["method"] for item in runtime_strategy.protections} == {
        "MaxDrawdown", "StoplossGuard", "CooldownPeriod",
    }


def test_real_strategy_tags_explain_unchanged_entry_and_exit_masks(runtime_strategy):
    data = pd.DataFrame({
        "ema_fast": [95.0, 105.0, 95.0, 105.0, float("nan")],
        "ema_slow": [100.0] * 5,
        "adx": [30.0, 15.0, 15.0, 30.0, float("nan")],
        "death_cross": [True, False, True, False, False],
        "golden_cross": [False, False, False, True, False],
    })
    entered = runtime_strategy.populate_entry_trend(data.copy(), {})
    assert entered["enter_long"].tolist() == [0, 0, 0, 1, 0]
    assert entered["enter_tag"].tolist() == ["", "", "", "s1_trend_entry", ""]
    exited = runtime_strategy.populate_exit_trend(data.copy(), {})
    assert exited["exit_long"].tolist() == [1, 1, 1, 0, 0]
    assert exited["exit_tag"].tolist() == [
        "s1_death_cross", "s1_adx_weak", "s1_death_cross", "", "",
    ]


@pytest.mark.parametrize("mode", ["backtest", "hyperopt", "util_exchange", "util_no_exchange"])
def test_historical_modes_do_not_consult_present_day_ai(runtime_strategy, monkeypatch, mode):
    from unittest.mock import MagicMock
    from freqtrade.enums import RunMode

    runtime_strategy.config["runmode"] = RunMode(mode)
    transport = MagicMock(side_effect=AssertionError("historical replay must not call current AI"))
    monkeypatch.setattr("urllib.request.urlopen", transport)
    result = runtime_strategy.confirm_trade_entry(
        pair="BTC/USDT", order_type="limit", amount=1.0, rate=100.0,
        time_in_force="GTC", current_time=NOW, entry_tag=None, side="long",
    )
    assert result is True
    transport.assert_not_called()


@pytest.mark.parametrize("mode", ["backtest", "hyperopt", "util_exchange", "util_no_exchange"])
def test_pure_base_historical_guard_does_not_depend_on_freqtrade(monkeypatch, mode):
    from unittest.mock import MagicMock
    from strategies.base import _PureStrategyBase

    strategy = _PureStrategyBase()
    strategy.config = {"runmode": mode}
    transport = MagicMock(side_effect=TimeoutError("no current AI in historical replay"))
    monkeypatch.setattr("urllib.request.urlopen", transport)
    assert strategy.confirm_trade_entry(
        pair="BTC/USDT", order_type="limit", amount=1.0, rate=100.0,
        time_in_force="GTC", current_time=NOW, entry_tag=None, side="long",
    ) is True
    transport.assert_not_called()


def test_actual_lookahead_entrypoint_uses_nontrading_veto_guard(runtime_strategy, monkeypatch):
    from unittest.mock import MagicMock
    from freqtrade.commands.optimize_commands import start_lookahead_analysis
    from freqtrade.enums import RunMode
    from freqtrade.optimize.analysis.lookahead_helpers import LookaheadAnalysisSubFunctions

    modes = []
    transport = MagicMock(side_effect=AssertionError("lookahead must not consult present-day AI"))

    def setup_configuration(args, mode):
        modes.append(mode)
        return {**runtime_strategy.config, "runmode": mode}

    def run_analysis(config):
        runtime_strategy.config = config
        assert runtime_strategy.confirm_trade_entry(
            pair="BTC/USDT", order_type="limit", amount=1.0, rate=100.0,
            time_in_force="GTC", current_time=NOW, entry_tag=None, side="long",
        ) is True

    monkeypatch.setattr("freqtrade.configuration.setup_utils_configuration", setup_configuration)
    monkeypatch.setattr(LookaheadAnalysisSubFunctions, "start", run_analysis)
    monkeypatch.setattr("urllib.request.urlopen", transport)
    start_lookahead_analysis({})
    assert modes == [RunMode.UTIL_NO_EXCHANGE]
    transport.assert_not_called()


def test_entry_veto_callback_body_is_identical_in_both_copies():
    bodies = []
    for path in [ROOT / "strategies/base.py", DEPLOY.parent / "base.py"]:
        tree = ast.parse(path.read_text())
        callback = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "confirm_trade_entry"
        )
        bodies.append([ast.dump(node, include_attributes=False) for node in callback.body[1:]])
    assert bodies[0] == bodies[1]


def test_pure_and_deployed_stop_helpers_cannot_drift():
    source_tree = ast.parse(Path(source.__file__).read_text())
    deploy_tree = ast.parse(DEPLOY.read_text())
    bodies = []
    for tree in [source_tree, deploy_tree]:
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "long_stop_price")
        bodies.append(ast.dump(function, include_attributes=False))
    assert bodies[0] == bodies[1]
