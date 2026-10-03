#!/usr/bin/env bash
# Start the Hemodynamic Concepts App on http://127.0.0.1:${PORT:-8765}
# Usage: scripts/run_app.sh [--port 8765] [--device auto|cpu|cuda]
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-/home/kyei/Document/Class-Project/.venv/bin/python}"
[ -x "$PY" ] || PY=python3
exec "$PY" app/server.py "$@"
