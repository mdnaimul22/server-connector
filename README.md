# Termux MCP Server

A small MCP-over-HTTP server for Android Termux. Includes command execution, background jobs, file operations, system information, and a bounded health/decision monitor.

## Requirements

- Android with Termux (F-Droid or official Termux source)
- Network access for package installation
- Python 3 and curl (installed by `install.sh`)

## Install on a new Termux device

```sh
pkg install -y git
 git clone YOUR_GITHUB_REPOSITORY_URL termux-mcp-server
cd termux-mcp-server
chmod +x install.sh
./install.sh
./start.sh
curl http://127.0.0.1:8765/health
```

The installer generates a unique `MCP_TOKEN` in `.env`. Keep `.env` private and never commit it. The server listens on port 8765 by default. It is intended for trusted local use; `run_command` executes shell commands with the permissions of the Termux app user. Do not expose it to the public internet without a secure authenticated tunnel/proxy and firewall controls.

## Commands

- Start/recover: `./start.sh`
- Stop: `./stop.sh`
- Health: `curl http://127.0.0.1:8765/health`

Optional tunnel support is configured through `TUNNEL_CMD` in `.env`. Configure the tunnel separately on each device; never commit tunnel credentials or private URLs.

## Security notes

- `.env`, logs, backups, runtime state, and Python caches are excluded from Git.
- Give each device a different `MCP_TOKEN`.
- The supervisor restarts the server and monitor after exits; this is not a substitute for Android boot configuration.
- Review the code before enabling remote access. Anyone with a valid token can invoke powerful shell and file tools.
