#!/usr/bin/env sh
cd "$(dirname "$0")"
if [ -f supervisor.pid ]; then
  pid="$(cat supervisor.pid)"
  kill "$pid" 2>/dev/null || true
fi
for pattern in 'sh ./supervisor.sh' 'python server.py' 'python3 server.py' 'python engine.py daemon' 'python3 engine.py daemon' 'logs/ngrok.yml'; do
  if command -v pkill >/dev/null 2>&1; then pkill -f "$pattern" 2>/dev/null || true; fi
done
rm -f supervisor.pid
if command -v termux-wake-unlock >/dev/null 2>&1; then termux-wake-unlock 2>/dev/null || true; fi
echo "Server Connector stop requested."
