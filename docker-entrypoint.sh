#!/usr/bin/env bash
set -e

echo "==> Applying database migrations"
python manage.py migrate --noinput

echo "==> Seeding demo data (idempotent)"
python manage.py seed_demo_data || echo "seed_demo_data skipped"

echo "==> Collecting static files"
python manage.py collectstatic --noinput

echo "==> Starting: $*"
exec "$@"
