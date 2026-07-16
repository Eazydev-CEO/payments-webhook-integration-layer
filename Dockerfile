# syntax=docker/dockerfile:1
FROM python:3.12-slim

# ---- Python runtime hygiene ------------------------------------------------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    DB_DIR=/data

WORKDIR /app

# System deps kept minimal; slim image already has what SQLite needs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Persist SQLite + collected static outside the image layer.
RUN mkdir -p /data /app/staticfiles

EXPOSE 8000

# Entrypoint runs migrations, seeds admin, collects static, then serves.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]
