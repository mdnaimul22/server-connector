#!/usr/bin/env sh
set -u
cd "$(dirname "$0")"
if [ ! -f .env ]; then echo "ERROR: .env missing" >&2; exit 1; fi
set -a; . ./.env; set +a
if [ -z "${MCP_TOKEN:-}" ] || [ "$MCP_TOKEN" = "REPLACE_WITH_A_LONG_RANDOM_SECRET" ]; then echo "ERROR: valid MCP_TOKEN missing from .env" >&2; exit 1; fi
PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then echo "ERROR: Python 3 is required" >&2; exit 1; fi
mkdir -p logs; printf '%s\n' "$$" > supervisor.pid
if command -v termux-wake-lock >/dev/null 2>&1; then termux-wake-lock 2>/dev/null || true; fi
loop() { service="$1"; shift; while :; do "$@" >> "logs/$service.log" 2>&1; rc=$?; echo "$(date '+%Y-%m-%dT%H:%M:%S%z') service=$service exit=$rc; restarting in 3s" >> logs/supervisor.log; sleep 3; done; }
loop server "$PYTHON" server.py &
if [ -n "${TUNNEL_CMD:-}" ]; then loop tunnel sh -c "$TUNNEL_CMD" & fi
loop engine "$PYTHON" self_engine.py daemon &
wait
