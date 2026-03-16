# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps into an isolated prefix so we can copy them cleanly
COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY . .

# Cloud Run injects PORT; default to 8080 for local testing
ENV PORT=8080 \
    APP_ENV=production \
    LOG_LEVEL=INFO \
    LOG_HTTP_REQUESTS=true \
    LOG_AUDIO_CHUNKS=false \
    LOG_TOOL_PAYLOADS=false

# Expose the port
EXPOSE 8080

# uvicorn with the Cloud Run recommended settings:
#   --workers 1   — Cloud Run scales via instances, not workers
#   --timeout-keep-alive 75  — must exceed the 60s WebSocket idle window
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 75"]
