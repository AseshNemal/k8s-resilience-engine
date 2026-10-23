#!/usr/bin/env bash
# Start the Member 4 Development Lab (offline, development only).
# Usage: ./scripts/dev-lab.sh [port]   (default 8741)
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8741}"
PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"
echo "Starting the Member 4 Development Lab on http://127.0.0.1:${PORT} (offline; no cluster is read or written)"
DEV_LAB_PORT="$PORT" exec "$PYTHON" -m experiments.member4_dev_lab
