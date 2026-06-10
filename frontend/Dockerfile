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

COPY frontend/requirements.txt /build/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install --prefix=/install -r /build/requirements.txt

FROM python:3.14-slim-bookworm AS runtime

WORKDIR /app/frontend

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:${PATH}" \
    BACKEND_URL=http://backend:8000 \
    NICEGUI_HOST=0.0.0.0 \
    NICEGUI_PORT=8181

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* && \
    groupadd --system appgroup && \
    useradd --system --gid appgroup --create-home --home-dir /home/appuser appuser

COPY --from=builder /install /usr/local
COPY --chown=appuser:appgroup frontend/main.py /app/frontend/main.py
COPY --chown=appuser:appgroup frontend/auth.py /app/frontend/auth.py

# NiceGUI writes its local state here when persistent storage is enabled.
RUN mkdir -p /app/frontend/.nicegui && \
    chown -R appuser:appgroup /app/frontend && \
    chmod -R 775 /app/frontend/.nicegui

USER appuser

EXPOSE 8181

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8181/ >/dev/null || exit 1

CMD ["python", "main.py"]
