"""Veto gate: HTTP call to the AI service /veto endpoint.

Extracted from the freqtrade strategy so the logic can be unit-tested
without a freqtrade runtime.  The strategy's confirm_trade_entry delegates
here; tests mock urllib.request.urlopen.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

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
