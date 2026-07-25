#!/bin/bash
# Runs Odysseus on a fixed, non-default port (chosen once, not re-randomized
# per run) so it doesn't collide with anything else using the common 7000
# default — e.g. Claude Code's own preview/dev server.
#
# Usage:
#   ./scripts/run-odysseus.sh          # foreground
#   nohup ./scripts/run-odysseus.sh &  # background, survives terminal close
#
# For "always running" (auto-restart on crash, start on boot), use this
# script's PORT value in odysseus-ui.service's ExecStart, then run
# ./install-service.sh — that gives you a real systemd service instead of a
# background shell process.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

PORT=24950
HOST=127.0.0.1

# internal_api_base() (src/constants.py) reads APP_PORT for the app's own
# loopback calls — must match the port actually bound below, or internal
# HTTP self-calls (agent tools, MCP, etc.) target the wrong port.
export APP_PORT="$PORT"

exec ./venv/bin/python -m uvicorn app:app --host "$HOST" --port "$PORT"
