#!/bin/sh
# Sentinel AI service entrypoint.
# Run any pending migrations (none yet — DB schema is created by SQLAlchemy on
# first request via Base.metadata.create_all), then exec uvicorn so the
# process replaces this shell and signals propagate cleanly.
set -e

# Honor SCHEDULER_ENABLED env var (default: on). The FastAPI lifespan in
# app/main.py reads it at startup.
export SCHEDULER_ENABLED="${SCHEDULER_ENABLED:-true}"

# Bind to loopback only — freqtrade (also on host network) reaches us at
# 127.0.0.1:8000. Avoiding 0.0.0.0 keeps the endpoint off the LAN.
exec uvicorn app.main:app \
    --host "${AI_SERVICE_HOST:-127.0.0.1}" \
    --port "${AI_SERVICE_PORT:-8000}" \
    --log-level "${AI_SERVICE_LOG_LEVEL:-info}"