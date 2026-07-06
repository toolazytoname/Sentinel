"""StrategyBase — runtime version for freqtrade image.

This is a self-contained copy of `strategies/base.py` because the freqtrade
Docker image does NOT mount the `strategies/` Python package — it only has
the strategies from `deploy/user_data/strategies/`. Any change here MUST be
mirrored to `strategies/base.py`.

When editing:
  1. Update strategies/base.py (tested, single source of truth for tests/hyperopt)
  2. Copy the change here (runtime inside freqtrade container)
  3. Run `pytest` to verify
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from freqtrade.strategy import IStrategy

logger = logging.getLogger(__name__)


# --- Constants (mirror strategies/veto_gate.py) ---
DEFAULT_AI_SERVICE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_S = 3


def check_veto(
    strategy: str,
    pair: str,
    *,
    ai_service_url: str = DEFAULT_AI_SERVICE_URL,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> bool:
    """Return True (allow entry) or False (block entry).

    Fail-open: any network error, timeout, or malformed response defaults
    to True so the AI service can never block trading due to its own failure.
    """
    url = f"{ai_service_url}/veto?strategy={strategy}&pair={pair}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read())
        decision = body.get("decision", "PASS")
        if decision == "VETO":
            reason = body.get("reason", "no reason provided")
            logger.warning("VETO blocked entry %s %s: %s", strategy, pair, reason)
            return False
        return True
    except Exception as exc:
        logger.warning(
            "AI veto service unreachable (%s) — defaulting PASS for %s/%s",
            exc, strategy, pair,
        )
        return True


class StrategyBase(IStrategy):
    """Sentinel runtime strategy base.

    Thin base: confirms entries via the AI veto endpoint, defaults to PASS
    on any failure (ADR-002 fail-open). Subclasses override populate_* and
    custom_stoploss.
    """
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = False
    stoploss = -0.10

    ai_service_url: str = DEFAULT_AI_SERVICE_URL
    veto_timeout_s: int = DEFAULT_TIMEOUT_S

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
        """Delegate to check_veto. Subclasses MUST NOT override this."""
        return check_veto(
            strategy=self.__class__.__name__,
            pair=pair,
            ai_service_url=self.ai_service_url,
            timeout=self.veto_timeout_s,
        )

    def custom_stoploss(self, *args, **kwargs) -> float:
        """Default: static stoploss. Subclasses override for trailing/hard-stop."""
        return float(self.stoploss)