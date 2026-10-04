# TrustMesh control plane - production container.
#
# Deployable to any container host with SSE support (Cloud Run, Fly.io, Render, Railway).
# It is deliberately NOT suitable for a serverless-function host such as Vercel: the
# demonstration run holds a request open far longer than a function timeout allows, and
# the SSE stream is a long-lived response.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is used by the container healthcheck below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so application edits do not invalidate the wheel layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Non-root: the container never needs privileges.
RUN useradd --create-home --uid 10001 trustmesh \
    && mkdir -p /data \
    && chown -R trustmesh:trustmesh /app /data
USER trustmesh

# SQLite lives here. Mount a volume at /data to persist it across restarts; without a
# volume the database is ephemeral and the registry is re-seeded on boot (which is safe,
# because the seeder is idempotent).
ENV DATABASE_URL=sqlite:////data/trustmesh.db \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health/ready" || exit 1

# exec form so the platform can send SIGTERM to uvicorn directly for a clean shutdown.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
