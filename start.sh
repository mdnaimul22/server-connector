#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -f .env ]; then echo "ERROR: .env is missing; run ./install.sh first" >&2; exit 1; fi
set -a; . ./.env; set +a
if [ -z "${MCP_TOKEN:-}" ] || [ "$MCP_TOKEN" = "REPLACE_WITH_A_LONG_RANDOM_SECRET" ]; then echo "ERROR: set a unique MCP_TOKEN in .env" >&2; exit 1; fi
mkdir -p logs
if [ -f supervisor.pid ] && kill -0 "$(cat supervisor.pid)" 2>/dev/null; then echo "Supervisor already running (PID $(cat supervisor.pid))"
else
  rm -f supervisor.pid
  if command -v setsid >/dev/null 2>&1; then nohup setsid sh ./supervisor.sh >> logs/supervisor-launch.log 2>&1 </dev/null &
  else nohup sh ./supervisor.sh >> logs/supervisor-launch.log 2>&1 </dev/null & fi
  echo "Supervisor start requested"
fi
if command -v curl >/dev/null 2>&1; then
  i=0
  while [ "$i" -lt 15 ]; do
    if curl -fsS --max-time 2 "http://127.0.0.1:${PORT:-8765}/health" >/dev/null 2>&1; then curl -fsS --max-time 2 "http://127.0.0.1:${PORT:-8765}/health"; echo; exit 0; fi
    sleep 1; i=$((i + 1))
  done
  echo "ERROR: server health did not recover within 15 seconds" >&2; tail -30 logs/supervisor.log logs/server.log 2>/dev/null || true; exit 1
else echo "curl not installed; health check skipped."; fi
