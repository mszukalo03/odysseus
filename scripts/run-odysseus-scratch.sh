#!/bin/bash
# Dev-verification runner: same as run-odysseus.sh but points at a throwaway
# scratch data dir and disables auth, so ad-hoc verification never touches
# the real dev data dir (data/) or requires logging in. Not for production use.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

PORT=24951
HOST=127.0.0.1

export APP_PORT="$PORT"
export ODYSSEUS_DATA_DIR="${ODYSSEUS_SCRATCH_DATA_DIR:-/tmp/odysseus-scratch-data}"
export AUTH_ENABLED=false
mkdir -p "$ODYSSEUS_DATA_DIR"

exec ./venv/bin/python -m uvicorn app:app --host "$HOST" --port "$PORT"
