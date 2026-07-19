#!/usr/bin/env bash
# One-command launch: creates the venv on first run, then serves ArchitectOS.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtualenv and installing dependencies..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

echo "ArchitectOS → http://127.0.0.1:${ARCHITECTOS_PORT:-8321}"
exec .venv/bin/python -m uvicorn backend.main:app \
  --host "${ARCHITECTOS_HOST:-127.0.0.1}" --port "${ARCHITECTOS_PORT:-8321}"
