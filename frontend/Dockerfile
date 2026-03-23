FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NICEGUI_HOST=0.0.0.0 \
    NICEGUI_PORT=8181

WORKDIR /app

COPY frontend/requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt

COPY frontend /app/frontend

EXPOSE 8181

CMD ["python", "frontend/main.py"]
