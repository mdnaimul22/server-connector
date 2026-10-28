#!/data/data/com.termux/files/usr/bin/sh
cd "$(dirname "$0")"
# Stop supervisors first so their restart loops cannot relaunch child services.
pkill -f "sh ./supervisor.sh" 2>/dev/null
sleep 1
pkill -f "python server.py" 2>/dev/null
pkill -f "python self_engine.py daemon" 2>/dev/null
pkill -f "ngrok http" 2>/dev/null
rm -f supervisor.pid
echo "MCP supervisor, server, engine and tunnel stop requested."
