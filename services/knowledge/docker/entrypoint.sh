#!/usr/bin/env sh
# Backend container entrypoint: apply migrations, then serve the API.
# `set -e` so a failed migration aborts the start instead of serving against an
# un-migrated database. `exec` hands PID 1 to uvicorn so Ctrl+C / SIGTERM stop
# it cleanly.
set -e

echo "[entrypoint] Applying database migrations (alembic upgrade head)..."
python -m alembic upgrade head

echo "[entrypoint] Starting knowledge API on ${API_HOST:-0.0.0.0}:${API_PORT:-8000}..."
exec python run_api.py
