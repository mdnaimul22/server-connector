#!/data/data/com.termux/files/usr/bin/sh
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  echo "ERROR: ~/mcp/.env is missing"
  exit 1
fi
set -a
. ./.env
set +a
if [ -z "${MCP_TOKEN:-}" ]; then
  echo "ERROR: MCP_TOKEN is missing from .env"
  exit 1
fi
mkdir -p logs
if [ -f supervisor.pid ] && kill -0 "$(cat supervisor.pid)" 2>/dev/null; then
  echo "Supervisor already running (PID $(cat supervisor.pid))"
else
  rm -f supervisor.pid
  nohup setsid sh ./supervisor.sh >> logs/supervisor-launch.log 2>&1 </dev/null &
  echo "Supervisor start requested"
fi
i=0
while [ "$i" -lt 15 ]; do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PORT:-8765}/health" >/dev/null 2>&1; then
    echo "HEALTH_OK"
    curl -fsS --max-time 2 "http://127.0.0.1:${PORT:-8765}/health"
    echo
    echo "CORE_PROCESSES:"
    ps -A -o pid,ppid,args | grep -E 'supervisor\.sh|python server\.py|self_engine\.py daemon|ngrok http' | grep -v grep
    exit 0
  fi
  sleep 1
  i=$((i + 1))
done
echo "ERROR: server health did not recover within 15 seconds"
tail -30 logs/supervisor.log logs/server.log 2>/dev/null
exit 1
