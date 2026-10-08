#!/usr/bin/env bash
# One-command start: creates the venv on first run, then serves API + dashboard on :8000
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r backend/requirements.txt
fi
cd backend
exec ../.venv/bin/uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" "$@"
