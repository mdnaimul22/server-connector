#!/usr/bin/env sh
cd "$(dirname "$0")"
if [ -f supervisor.pid ]; then pid="$(cat supervisor.pid)"; kill "$pid" 2>/dev/null || true; fi
for pattern in 'python server.py' 'python3 server.py' 'python self_engine.py daemon' 'python3 self_engine.py daemon'; do
  if command -v pkill >/dev/null 2>&1; then pkill -f "$pattern" 2>/dev/null || true; fi
done
rm -f supervisor.pid
echo "Server Connector stop requested."
