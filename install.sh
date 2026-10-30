#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  if command -v pkg >/dev/null 2>&1; then pkg install -y python curl
  elif command -v apt-get >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then apt-get update && apt-get install -y python3 curl
    elif command -v sudo >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y python3 curl
    else echo "Install python3 and curl, then rerun." >&2; exit 1; fi
  elif command -v dnf >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then dnf install -y python3 curl
    elif command -v sudo >/dev/null 2>&1; then sudo dnf install -y python3 curl
    else echo "Install python3 and curl, then rerun." >&2; exit 1; fi
  else echo "Install Python 3 and curl with your package manager." >&2; exit 1; fi
fi
PYTHON="$(command -v python3 || command -v python)"
mkdir -p logs
"$PYTHON" engine.py init-config
chmod +x start.sh stop.sh supervisor.sh install.sh status.sh
echo "Install complete. Edit config.yml if needed, then start with: ./start.sh"
