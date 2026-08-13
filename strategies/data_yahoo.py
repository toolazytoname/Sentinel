"""Download Yahoo daily OHLCV for the frozen USDT universe (research only)."""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from strategies.universe import USDT_SPOT_UNIVERSE, yahoo_symbol

YAHOO_UA = "Mozilla/5.0 (compatible; SentinelResearch/1.0)"


def fetch_yahoo_daily(symbol: str, start_ts: int, end_ts: int) -> pd.DataFrame:
    """Fetch daily OHLCV. Raises on HTTP/parse failure."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval=1d&period1={start_ts}&period2={end_ts}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": YAHOO_UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    result = payload["chart"]["result"][0]
    ts = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    frame = pd.DataFrame(
        {
            "open": quote["open"],
            "high": quote["high"],
            "low": quote["low"],
            "close": quote["close"],
            "volume": quote["volume"],
        },
        index=pd.to_datetime(ts, unit="s", utc=True).tz_convert(None).normalize(),
    )
    return frame.dropna(subset=["close"])


def fetch_universe(
    start: str = "2020-06-01",
    end: str | None = None,
    *,
    pause_s: float = 0.25,
) -> dict[str, pd.DataFrame]:
    """Download all pairs. Missing symbols are skipped (delist / ticker change)."""
    start_ts = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    if end is None:
        end_ts = int(datetime.now(timezone.utc).timestamp())
    else:
        end_ts = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp())
    out: dict[str, pd.DataFrame] = {}
    for pair in USDT_SPOT_UNIVERSE:
        symbol = yahoo_symbol(pair)
        try:
            out[pair] = fetch_yahoo_daily(symbol, start_ts, end_ts)
        except Exception:
            continue
        time.sleep(pause_s)
    return out
