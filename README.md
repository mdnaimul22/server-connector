# Server Connector

A platform-independent, self-hostable MCP-over-HTTP server intended to connect ChatGPT-compatible MCP clients to tools on your own machine. The core is Python 3 and uses the standard library; Termux is supported but not required.

## Supported environments

- Ubuntu and other Debian-based Linux distributions
- Other Linux distributions with Python 3
- Android Termux

## Requirements

- Python 3.10 or newer
- `curl` for the launcher's health check (recommended)
- `git` to clone or update the repository

## Install and run

```sh
git clone https://github.com/mdnaimul22/server-connector.git
cd server-connector
chmod +x install.sh start.sh stop.sh
./install.sh
./start.sh
curl http://127.0.0.1:8765/health
```

The installer creates `.env` and generates a unique `MCP_TOKEN` if `.env` does not exist. Keep that file private. Stop with `./stop.sh`.

## Endpoint and configuration

- Health: `GET http://127.0.0.1:8765/health`
- MCP: `POST /mcp` with `Authorization: Bearer <MCP_TOKEN>`; the legacy `/mcp/<MCP_TOKEN>` path is also accepted.
- `HOST`: bind address; defaults to `127.0.0.1` for local-only access.
- `PORT`: listening port; defaults to `8765`.
- `MCP_TOKEN`: per-installation bearer secret.
- `TUNNEL_CMD`: optional HTTPS tunnel command. Configure credentials per host; do not commit them.

For remote access, use a trusted HTTPS tunnel or reverse proxy and configure the client with the externally reachable endpoint and authentication. Do not expose the server directly to the internet: its shell and file tools have the same privileges as the operating-system user running it.

## Portability

The core server uses Python's standard library and has no Termux-only Python dependencies. The launcher uses portable shell and Python 3; Android wake-lock support is enabled only when available. System-information output can vary by OS.

## Security

- `.env`, logs, backups, runtime state, and Python caches are excluded from Git.
- Use a unique, strong token per host. Rotate any token exposed or committed to Git history.
- Review and restrict the available tools before granting remote access; `run_command` executes arbitrary shell commands as the service account.
