#!/bin/sh
# Sentinel freqtrade startup script.
# Replaces ${API_PASSWORD} / ${API_JWT_SECRET} / webhook URL from env,
# then launches freqtrade trade.
#
# Per-bot overrides (S1 vs S2):
#   FREQTRADE_CONFIG_IN   default /freqtrade/user_data/config/dry-run.json
#   FREQTRADE_STRATEGY    default S1TrendFollow
#   FREQTRADE_DB_URL      default sqlite:///user_data/tradesv3.sqlite
set -e

export FREQTRADE_CONFIG_IN="${FREQTRADE_CONFIG_IN:-/freqtrade/user_data/config/dry-run.json}"
export FREQTRADE_STRATEGY="${FREQTRADE_STRATEGY:-S1TrendFollow}"
export FREQTRADE_DB_URL="${FREQTRADE_DB_URL:-sqlite:///user_data/tradesv3.sqlite}"
export FREQTRADE_LOGFILE="${FREQTRADE_LOGFILE:-/freqtrade/user_data/logs/freqtrade.log}"

python3 - <<'PYEOF'
import os

src_path = os.environ["FREQTRADE_CONFIG_IN"]
src = open(src_path).read()
dst = (
    src
    .replace("${API_PASSWORD}", os.environ.get("API_PASSWORD", ""))
    .replace("${API_JWT_SECRET}", os.environ.get("API_JWT_SECRET", ""))
    .replace("${AI_SERVICE_WEBHOOK_URL}", os.environ.get("AI_SERVICE_WEBHOOK_URL", "http://127.0.0.1:8000"))
)
open("/tmp/freqtrade-runtime.json", "w").write(dst)
PYEOF

exec freqtrade trade \
    --logfile "${FREQTRADE_LOGFILE}" \
    --db-url "${FREQTRADE_DB_URL}" \
    --config /tmp/freqtrade-runtime.json \
    --strategy "${FREQTRADE_STRATEGY}"
