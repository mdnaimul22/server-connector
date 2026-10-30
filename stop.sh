#!/usr/bin/env sh
cd "$(dirname "$0")"
if [ -f supervisor.pid ]; then
  pid="$(cat supervisor.pid)"
  kill "$pid" 2>/dev/null || true
fi
for pattern in 'sh ./supervisor.sh' 'python server.py' 'python3 server.py' 'python engine.py daemon' 'python3 engine.py daemon' 'python self_engine.py daemon' 'python3 self_engine.py daemon' 'bin/ngrok.yml' 'logs/ngrok.yml'; do
  if command -v pkill >/dev/null 2>&1; then pkill -f "$pattern" 2>/dev/null || true; fi
done
rm -f supervisor.pid self_engine_state.json
if command -v termux-wake-unlock >/dev/null 2>&1; then termux-wake-unlock 2>/dev/null || true; fi
echo "Server Connector stop requested."
