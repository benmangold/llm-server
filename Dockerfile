# ---------- Stage 1: build dependencies with Poetry ----------
FROM python:3.13-slim AS builder

ENV POETRY_VERSION=2.4.1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /app

# Copy only dependency manifests first to maximize layer caching
COPY pyproject.toml poetry.lock ./
RUN poetry install

# ---------- Stage 2: runtime ----------
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000

# curl is used only for the container HEALTHCHECK
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system appuser \
    && useradd --system --gid appuser --no-create-home appuser

WORKDIR /app

# Copy the prebuilt virtualenv from the builder stage (no Poetry in runtime)
COPY --from=builder /app/.venv ./.venv

# Copy application code
COPY app/ ./app/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# Multiple workers for production; tune --workers to your CPU count
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2 --no-server-header"]