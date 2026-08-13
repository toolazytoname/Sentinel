"""Yahoo fetch parses chart JSON (no live network)."""
from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch

from strategies.data_yahoo import fetch_universe, fetch_yahoo_daily


class _Resp:
    def __enter__(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1609459200, 1609545600],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [100.0, 101.0],
                                    "high": [102.0, 103.0],
                                    "low": [99.0, 100.0],
                                    "close": [101.0, 102.0],
                                    "volume": [1.0, 2.0],
                                }
                            ]
                        },
                    }
                ]
            }
        }
        self._buf = BytesIO(json.dumps(payload).encode())
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._buf.read()


def test_fetch_yahoo_daily_parses_ohlcv():
    with patch("strategies.data_yahoo.urllib.request.urlopen", return_value=_Resp()):
        df = fetch_yahoo_daily("BTC-USD", 1609459200, 1609545600)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert float(df["close"].iloc[-1]) == 102.0


def test_fetch_universe_skips_failures_and_maps_pairs():
    import pandas as pd

    frame = pd.DataFrame(
        {"open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]},
        index=pd.to_datetime(["2021-01-01"]),
    )

    def _fake(symbol, start_ts, end_ts):
        if symbol == "ETH-USD":
            raise RuntimeError("skip")
        return frame

    with patch("strategies.data_yahoo.fetch_yahoo_daily", side_effect=_fake):
        with patch("strategies.data_yahoo.time.sleep"):
            out = fetch_universe("2021-01-01", "2021-01-03", pause_s=0)
    assert "BTC/USDT" in out
    assert "ETH/USDT" not in out
    assert len(out) == 14
