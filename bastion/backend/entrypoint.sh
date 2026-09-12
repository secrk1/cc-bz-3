#!/bin/sh
set -e

echo "[entrypoint] running database migrations..."
alembic upgrade head

echo "[entrypoint] seeding initial data..."
python -m app.seed

echo "[entrypoint] starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
