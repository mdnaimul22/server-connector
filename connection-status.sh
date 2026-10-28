#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -f connection-profile.json ]; then
  echo 'Connection profile missing: connection-profile.json'
  exit 0
fi
printf '\n=== MCP connection details ===\n'
python - <<'PY'
import json
from pathlib import Path
p = json.loads(Path('connection-profile.json').read_text())
for label, key in [
    ('Public base URL', 'public_base_url'),
    ('MCP endpoint (POST)', 'mcp_endpoint'),
    ('Health check URL (GET)', 'health_url'),
    ('Alternative MCP endpoint', 'alternative_endpoint'),
    ('Local health URL', 'local_health_url'),
    ('Local MCP endpoint', 'local_mcp_endpoint'),
    ('Tunnel domain', 'tunnel_domain'),
]:
    if p.get(key):
        print(f'{label}: {p[key]}')
print('Authentication: use the token embedded in the MCP endpoint above.')
print('Alternative authentication: Authorization: Bearer <MCP_TOKEN>')
PY
REMOTE_HEALTH=$(python -c 'import json; print(json.load(open("connection-profile.json")).get("health_url", ""))')
if [ -n "$REMOTE_HEALTH" ] && command -v curl >/dev/null 2>&1; then
  printf '\n=== Remote Termux MCP status ===\n'
  if ! curl -fsS --max-time 8 "$REMOTE_HEALTH"; then
    echo 'UNREACHABLE: check the Termux server and ngrok tunnel.'
  fi
  printf '\n'
fi
