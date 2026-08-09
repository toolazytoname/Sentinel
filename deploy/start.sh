#!/bin/sh
# Sentinel freqtrade startup script.
# Replaces ${API_PASSWORD} and ${API_JWT_SECRET} placeholders from env,
# then launches freqtrade trade.
set -e

# Freqtrade reads nested settings from FREQTRADE__* environment variables.
# Keep the original Sentinel names working for existing local .env files,
# without printing or copying secrets into a tracked file.
if [ -n "${FREQTELEGRAM__TELEGRAM_TOKEN:-}" ] && [ -z "${FREQTRADE__TELEGRAM__TOKEN:-}" ]; then
    export FREQTRADE__TELEGRAM__TOKEN="$FREQTELEGRAM__TELEGRAM_TOKEN"
fi
if [ -n "${FREQTELEGRAM__CHAT_ID:-}" ] && [ -z "${FREQTRADE__TELEGRAM__CHAT_ID:-}" ]; then
    export FREQTRADE__TELEGRAM__CHAT_ID="$FREQTELEGRAM__CHAT_ID"
fi
if [ -n "${FREQTELEGRAM__ALLOWED_CHAT_IDS:-}" ] && [ -z "${FREQTRADE__TELEGRAM__ALLOWED_CHAT_IDS:-}" ]; then
    export FREQTRADE__TELEGRAM__ALLOWED_CHAT_IDS="$FREQTELEGRAM__ALLOWED_CHAT_IDS"
fi

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
