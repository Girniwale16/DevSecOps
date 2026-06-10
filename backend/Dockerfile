FROM python:3.14-slim-bookworm AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /build/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install --prefix=/install -r /build/requirements.txt

FROM python:3.14-slim-bookworm AS runtime

WORKDIR /app/backend

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* && \
    groupadd --system appgroup && \
    useradd --system --gid appgroup --create-home --home-dir /home/appuser appuser

COPY --from=builder /install /usr/local
COPY --chown=appuser:appgroup backend/app /app/backend/app

# Keep project metadata and generated files writable after the named volume is mounted.
RUN mkdir -p /app/backend/data/uploads /app/backend/data/exports && \
    chown -R appuser:appgroup /app/backend && \
    chmod -R 775 /app/backend/data

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/ >/dev/null || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
