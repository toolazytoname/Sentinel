"""StrategyBase: shared infrastructure for all Sentinel strategies.

Per docs/system/02-design.md §2.1:
  策略基类 `StrategyBase`（薄）：统一实现 confirm_trade_entry 查否决表、
  统一日志格式、统一 custom_stoploss 骨架

This base is **thin on purpose**. It does NOT define:
  - entry/exit signal logic (per-strategy)
  - populate_indicators (per-strategy — different indicators per strategy)
  - hyperopt parameter spaces (per-strategy)

It DOES define:
  - confirm_trade_entry → delegates to strategies.veto_gate.check_veto
  - canonical AI service URL / timeout constants
  - a uniform log prefix so audit trails can grep by [StrategyBase]
  - a default custom_stoploss that just returns -0.10 (each strategy
    overrides this with its own trailing/hard-stop logic)

When freqtrade is not installed (e.g. unit tests in a venv without the
freqtrade image), the import of IStrategy fails and StrategyBase degrades
to a plain Python class so indicator/logic tests can still run.
"""
from __future__ import annotations

import logging

from strategies.veto_gate import (
    DEFAULT_AI_SERVICE_URL,
    DEFAULT_TIMEOUT_S,
    check_veto,
)

logger = logging.getLogger(__name__)


# Names a subclass MUST set (kept as class attributes for clarity).
# Subclasses override these; the base only documents the contract.
REQUIRED_CLASS_ATTRS = (
    "timeframe",
    "can_short",
    "stoploss",
)


class _PureStrategyBase:
    """Pure-Python base usable without freqtrade.

    Holds the contract every Sentinel strategy must satisfy (entry/exit
    signals, AI veto integration, custom_stoploss) so tests can exercise
    the same surface as the freqtrade runtime.
    """

    # Defaults — subclasses override these.
    timeframe = "1d"
    can_short = False
    stoploss = -0.10

    # AI veto integration (see strategies.veto_gate)
    ai_service_url: str = DEFAULT_AI_SERVICE_URL
    veto_timeout_s: int = DEFAULT_TIMEOUT_S

    # Subclasses override these — the base just documents the contract.
    def populate_indicators(self, dataframe, metadata):  # pragma: no cover - abstract
        raise NotImplementedError

    def populate_entry_trend(self, dataframe, metadata):  # pragma: no cover - abstract
        raise NotImplementedError

    def populate_exit_trend(self, dataframe, metadata):  # pragma: no cover - abstract
        raise NotImplementedError

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time,
        entry_tag,
        side: str,
        **kwargs,
    ) -> bool:
        """Ask AI service for veto before each entry. Fail-open on error.

        This is the SINGLE canonical implementation. Subclasses must NOT
        reimplement — they inherit this and get the ADR-002 fail-open
        guarantee for free.
        """
        return check_veto(
            strategy=self.__class__.__name__,
            pair=pair,
            ai_service_url=self.ai_service_url,
            timeout=self.veto_timeout_s,
        )

    def custom_stoploss(self, *args, **kwargs) -> float:
        """Default: return static stoploss. Subclasses override for trailing/hard-stop."""
        return float(self.stoploss)


# --- freqtrade adapter: only loaded when freqtrade is installed ---

try:
    from freqtrade.strategy import IStrategy

    class StrategyBase(IStrategy, _PureStrategyBase):
        """Sentinel base strategy — extends freqtrade's IStrategy.

        Subclasses override populate_indicators / populate_entry_trend /
        populate_exit_trend / custom_stoploss; they inherit confirm_trade_entry
        and the unified AI-veto integration.
        """
        # Defaults; subclasses MUST override timeframe at minimum.
        INTERFACE_VERSION = 3
        timeframe = "1d"
        can_short = False
        stoploss = -0.10

        # We rely on signal exits, not ROI tables; subclasses may override.
        minimal_roi = {"0": 100}
        use_exit_signal = True

except ImportError:
    # freqtrade not installed (e.g. CI venv) — tests run against _PureStrategyBase.
    StrategyBase = _PureStrategyBase  # type: ignore[assignment,misc]