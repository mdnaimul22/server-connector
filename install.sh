#!/data/data/com.termux/files/usr/bin/sh
set -eu
cd "$(dirname "$0")"
pkg install -y python curl termux-tools
mkdir -p logs
if [ ! -f .env ]; then
  cp .env.example .env
  TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  sed "s|^MCP_TOKEN=.*|MCP_TOKEN=$TOKEN|" .env.example > .env
  echo "Created .env with a unique MCP_TOKEN. Keep it private."
else
  echo ".env already exists; leaving it unchanged."
fi
chmod 700 .env
chmod +x start.sh stop.sh supervisor.sh install.sh
echo "Install step complete. Start the server with: ./start.sh"
