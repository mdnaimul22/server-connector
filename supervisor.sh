#!/data/data/com.termux/files/usr/bin/sh
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  echo "ERROR: .env missing" >&2
  exit 1
fi
set -a
. ./.env
set +a
if [ -z "${MCP_TOKEN:-}" ]; then
  echo "ERROR: MCP_TOKEN missing from .env" >&2
  exit 1
fi
mkdir -p logs
echo $$ > supervisor.pid
termux-wake-lock 2>/dev/null
loop() {
  service="$1"
  shift
  while :; do
    "$@" >> "logs/$service.log" 2>&1
    rc=$?
    echo "$(date '+%Y-%m-%dT%H:%M:%S%z') service=$service exit=$rc; restarting in 3s" >> "logs/supervisor.log"
    sleep 3
  done
}
loop server python server.py &
if [ -n "${TUNNEL_CMD:-}" ]; then
  loop tunnel sh -c "$TUNNEL_CMD" &
fi
loop engine python self_engine.py daemon &
wait
