# server-connector

Minimal MCP server over HTTP to give ChatGPT, Claude, or any remote MCP client direct shell and file access to an Android phone (Termux) or a Linux machine.

Built specifically with the Python standard library only. No `pip install`, no virtualenvs, and no C-extension wheel builds that break on Termux.

## Why it works this way

- **Stdlib only:** Runs out of the box on stock Python 3 (`http.server`, `subprocess`, `urllib`).
- **Single config (`config.yml`):** Everything lives in one file. No `.env` quoting issues or scattered tunnel configs.
- **Auto tunnel setup:** If `ngrok` isn't installed on the machine, `./start.sh` pulls the right static binary (`amd64` / `arm64` / `arm`) into `./bin/ngrok` and generates the runtime config with public DNS resolvers (`8.8.8.8`, `1.1.1.1`) so Termux DNS doesn't fail.

## Setup

```sh
git clone https://github.com/mdnaimul22/server-connector.git
cd server-connector
chmod +x *.sh
./install.sh
```

Open `config.yml` and drop in your ngrok `authtoken` and static `tunnel_domain`:

```yaml
authtoken: "your_ngrok_authtoken"
tunnel_domain: "your-domain.ngrok-free.dev"
MCP_TOKEN: "auto-generated-or-your-own-secret"
HOST: "127.0.0.1"
PORT: 8765
TUNNEL_CMD: ""
```

Start it:

```sh
./start.sh
```

This starts the background daemon (`engine.py`, `server.py`, and `ngrok`) and prints the live endpoints right in the terminal:

```text
MCP Connector URL (POST): https://your-domain.ngrok-free.dev/mcp/<MCP_TOKEN>
Alt MCP Endpoint (POST) : https://your-domain.ngrok-free.dev/mcp
Alt Auth Header         : Authorization: Bearer <MCP_TOKEN>
```

Paste the **MCP Connector URL** into ChatGPT / Claude MCP settings and you're connected.

## Commands

- `./start.sh` — start (or restart after editing `config.yml`) and print status
- `./status.sh` — check live local + remote tunnel health and print URLs
- `./stop.sh` — stop supervisor, server, engine, and tunnel processes
- `./upgrade.py check | apply` — validate and hot-swap `server.py.candidate` with automatic rollback if health checks fail

## Running on multiple machines

Free ngrok accounts allow 1 static domain and 1 active agent session per account. To run this across multiple devices (e.g., Termux on phone + Linux desktop + VPS) at the same time:

1. Use a separate free ngrok account (`authtoken` + `tunnel_domain`) per machine, or
2. Set `TUNNEL_CMD` in `config.yml` to your own tunnel command (e.g., `cloudflared`, `ssh -R`), or `"none"` if you're reverse-proxying it yourself.

## Files

```text
config.yml          local config & secrets (gitignored)
config.example.yml  template config
server.py           MCP JSON-RPC server & tools
engine.py           config loader, process supervisor & health watchdog
upgrade.py          safe atomic updater with rollback
start.sh / stop.sh  service control
status.sh           terminal status dashboard
```
