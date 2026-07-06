#!/bin/sh
# Sentinel freqtrade startup script.
# Replaces ${API_PASSWORD} and ${API_JWT_SECRET} placeholders from env,
# then launches freqtrade trade.
set -e

python3 - <<'PYEOF'
import os, sys

src = open("/freqtrade/user_data/config/dry-run.json").read()
dst = (
    src
    .replace("${API_PASSWORD}", os.environ.get("API_PASSWORD", ""))
    .replace("${API_JWT_SECRET}", os.environ.get("API_JWT_SECRET", ""))
    .replace("${AI_SERVICE_WEBHOOK_URL}", os.environ.get("AI_SERVICE_WEBHOOK_URL", "http://127.0.0.1:8000"))
)
open("/tmp/dry-run-runtime.json", "w").write(dst)
PYEOF

exec freqtrade trade \
    --logfile /freqtrade/user_data/logs/freqtrade.log \
    --db-url "sqlite:///user_data/tradesv3.sqlite" \
    --config /tmp/dry-run-runtime.json \
    --strategy S1TrendFollow
