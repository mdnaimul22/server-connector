#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
export PATH="$(pwd)/bin:$PATH"
PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then echo "ERROR: Python 3 is required; run ./install.sh first" >&2; exit 1; fi
"$PYTHON" engine.py init-config
eval "$("$PYTHON" engine.py env)"
if [ -z "${MCP_TOKEN:-}" ] || [ "$MCP_TOKEN" = "REPLACE_WITH_A_LONG_RANDOM_SECRET" ]; then
  echo "ERROR: set a valid MCP_TOKEN in config.yml" >&2
  exit 1
fi
mkdir -p logs
if [ -f supervisor.pid ] && kill -0 "$(cat supervisor.pid)" 2>/dev/null; then
  echo "Restarting running supervisor to apply current config.yml ..."
  sh ./stop.sh >/dev/null 2>&1 || true
  sleep 1
fi
rm -f supervisor.pid self_engine_state.json
if command -v setsid >/dev/null 2>&1; then
  nohup setsid sh ./supervisor.sh >/dev/null 2>&1 </dev/null &
else
  nohup sh ./supervisor.sh >/dev/null 2>&1 </dev/null &
fi
echo "Supervisor start requested"
i=0
while [ "$i" -lt 15 ]; do
  if "$PYTHON" -c "import sys, engine; sys.exit(0 if engine.health().get('ok') else 1)" 2>/dev/null; then
    if [ -n "${TUNNEL_DOMAIN:-}" ] && [ -n "${TUNNEL_CMD:-}" ]; then
      j=0
      while [ "$j" -lt 8 ]; do
        if "$PYTHON" -c "import sys, engine; sys.exit(0 if engine.check_url_health('https://${TUNNEL_DOMAIN}/health', 2).get('ok') else 1)" 2>/dev/null; then
          break
        fi
        sleep 1
        j=$((j + 1))
      done
    fi
    sh ./status.sh
    exit 0
  fi
  sleep 1
  i=$((i + 1))
done
echo "ERROR: server health did not recover within 15 seconds" >&2
tail -30 logs/connector.log 2>/dev/null || true
exit 1
