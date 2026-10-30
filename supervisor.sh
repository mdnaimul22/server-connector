#!/usr/bin/env sh
set -u
cd "$(dirname "$0")"
export PATH="$(pwd)/bin:$PATH"
PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then echo "ERROR: Python 3 is required" >&2; exit 1; fi
if [ ! -f config.yml ]; then "$PYTHON" engine.py init-config >/dev/null 2>&1 || true; fi
eval "$("$PYTHON" engine.py env)"
if [ -z "${MCP_TOKEN:-}" ] || [ "$MCP_TOKEN" = "REPLACE_WITH_A_LONG_RANDOM_SECRET" ]; then
  echo "ERROR: valid MCP_TOKEN missing from config.yml" >&2
  exit 1
fi
mkdir -p logs
printf '%s\n' "$$" > supervisor.pid
trap 'kill 0 2>/dev/null || true; rm -f supervisor.pid; exit 0' INT TERM
if command -v termux-wake-lock >/dev/null 2>&1; then termux-wake-lock 2>/dev/null || true; fi
loop() {
  service="$1"; shift
  while :; do
    "$@" >> "logs/$service.log" 2>&1
    rc=$?
    echo "$(date '+%Y-%m-%dT%H:%M:%S%z') service=$service exit=$rc; restarting in 3s" >> logs/supervisor.log
    sleep 3
  done
}
loop server "$PYTHON" server.py &
if [ -n "${TUNNEL_CMD:-}" ]; then
  TUNNEL_BIN="$(printf '%s' "${TUNNEL_CMD%% *}" | tr -d '"'"'")"
  if command -v "$TUNNEL_BIN" >/dev/null 2>&1 || [ -x "$TUNNEL_BIN" ]; then
    loop tunnel sh -c "$TUNNEL_CMD" &
  else
    echo "$(date '+%Y-%m-%dT%H:%M:%S%z') tunnel binary '$TUNNEL_BIN' not found in PATH; skipping local tunnel launch" >> logs/tunnel.log
  fi
fi
loop engine "$PYTHON" engine.py daemon &
(sleep 3; "$PYTHON" engine.py status >> logs/supervisor.log 2>&1 || true) &
wait
