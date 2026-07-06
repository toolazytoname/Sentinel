# Sentinel AI service image.
#
# Single-stage slim build is sufficient — the codebase is small and image
# pulls dominate wall-clock more than layer caching would.
#
# Build:   docker build -t sentinel-ai:dev ai-service
# Run:     docker run --rm --network host -e AGNES_API_KEY=... -v sentinel-ai-db:/app/data sentinel-ai:dev
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install OS deps: curl for healthcheck, ca-certificates for HTTPS to LLM.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first so the layer caches when only source changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copy source.
COPY app ./app
COPY scripts ./scripts

# Create a non-root user and writable data dir for the SQLite database.
RUN useradd --create-home --shell /bin/bash sentinel \
    && mkdir -p /app/data \
    && chown -R sentinel:sentinel /app
USER sentinel

# Default DATABASE_URL points inside the persistent data dir; override at
# run time to use an external volume mount or a different path.
ENV DATABASE_URL=sqlite:////app/data/sentinel.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["/app/scripts/start.sh"]