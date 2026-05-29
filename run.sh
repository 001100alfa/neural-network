#!/usr/bin/env bash
# AIO - portable launcher for macOS / Linux.
# No installation required: pure Python stdlib, run from this folder.
# Requirement: Python 3.11+ on PATH.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$(pwd)/src"

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "[AIO] Python 3.11+ not found on PATH."
  exit 1
fi
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info[:2]>=(3,11) else 1)'; then
  echo "[AIO] Python 3.11+ is required. Detected: $("$PY" --version)"
  exit 1
fi

echo "[AIO] Starting the portable web dashboard at http://localhost:8765 ..."
exec "$PY" -m aio --web --open "$@"
