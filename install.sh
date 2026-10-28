#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
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
mkdir -p logs
if [ ! -f .env ]; then
  cp .env.example .env
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  TOKEN="$TOKEN" python3 - <<'PY'
from pathlib import Path
import os
p = Path('.env')
p.write_text(p.read_text().replace('MCP_TOKEN=REPLACE_WITH_A_LONG_RANDOM_SECRET', 'MCP_TOKEN=' + os.environ['TOKEN']))
PY
  echo "Created .env with a unique MCP_TOKEN. Keep it private."
else echo ".env already exists; leaving it unchanged."; fi
chmod 600 .env
chmod +x start.sh stop.sh supervisor.sh install.sh
echo "Install complete. Start with: ./start.sh"
