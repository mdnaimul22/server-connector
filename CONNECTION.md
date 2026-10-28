# Live Connection Profile

This file describes the **currently running Termux instance** observed on 2026-10-29. The JSON equivalent is [`connection-profile.json`](connection-profile.json).

## Current connection details

- **Public base URL:** https://whistly-sabulous-jeff.ngrok-free.dev
- **Health check (GET, no token required):** https://whistly-sabulous-jeff.ngrok-free.dev/health
- **MCP endpoint (POST):** `https://whistly-sabulous-jeff.ngrok-free.dev/mcp/38ee301723dbb6ad4e6e9acc1f03920b1bc937bcef7ea009`
- **Alternative MCP endpoint (POST):** `https://whistly-sabulous-jeff.ngrok-free.dev/mcp`
- **Alternative authentication header:** `Authorization: Bearer <MCP_TOKEN>`
- **Local health check:** `http://127.0.0.1:8765/health`
- **Local MCP endpoint:** `http://127.0.0.1:8765/mcp` with `Authorization: Bearer <MCP_TOKEN>`
- **Port:** `8765`
- **Active server directory:** `~/mcp` (the running process is not launched from the repository checkout directory)
- **Tunnel:** ngrok, domain `whistly-sabulous-jeff.ngrok-free.dev`

## How to connect

1. Configure the MCP client to use the **MCP endpoint** above with HTTP `POST`.
2. If the client supports custom headers, use `https://whistly-sabulous-jeff.ngrok-free.dev/mcp` and set `Authorization: Bearer <MCP_TOKEN>` from the active installation's `.env`.
3. If the client only accepts a URL and cannot set headers, use the token-path MCP endpoint above.
4. To check reachability independently of MCP authentication, request the health URL. A healthy response contains `"ok": true`.

## Status and limitations

The health check succeeded during the profile update. This file is a **snapshot**, not a live status feed; check the health URL to determine current availability. The ngrok domain must still be online and forwarding to local port `8765`.

## Security

The token-path endpoint contains a credential. Treat this file as secret and keep the repository private. Do not paste the full MCP endpoint into public issues, screenshots, or public documentation. If the repository ever becomes public or the endpoint leaks, rotate `MCP_TOKEN` and update the client configuration.
