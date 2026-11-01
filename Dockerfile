# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIGEON_DB_PATH=/data/pigeon.db

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install .

RUN useradd --create-home --uid 10001 bot \
    && mkdir -p /data \
    && chown bot:bot /data
USER bot

VOLUME ["/data"]

CMD ["python", "-m", "pigeon"]
