# Server Connector

A zero-dependency, self-hostable MCP-over-HTTP server that connects AI agents (such as ChatGPT or Claude) directly to tools and terminals on your own Linux or Android Termux machine.

## Supported environments

- Ubuntu, Debian, Fedora, and other Linux distributions with Python 3
- Android Termux

## Requirements

- Python 3.10 or newer (standard library only; zero external pip packages required)
- `ngrok` (optional, for remote HTTPS tunnel access)

## Quick Start (Set & Forget)

```sh
git clone https://github.com/mdnaimul22/server-connector.git
cd server-connector
chmod +x install.sh start.sh stop.sh status.sh
./install.sh
```

Edit `config.yml` (the single source of configuration) to set your tunnel credentials and host settings:

```yaml
authtoken: "YOUR_NGROK_AUTHTOKEN"
tunnel_domain: "your-domain.ngrok-free.dev"
MCP_TOKEN: "auto-generated-by-install-or-set-your-own"
HOST: "127.0.0.1"
PORT: 8765
TUNNEL_CMD: ""
```

Then start the service:

```sh
./start.sh
```

On startup, `./start.sh` (or `./status.sh` at any time) prints the live MCP Connector URL (`https://<tunnel_domain>/mcp/<MCP_TOKEN>`), alternative Bearer authentication details, and live local/remote health status directly in the terminal.

Stop the service at any time with:

```sh
./stop.sh
```

## Autonomous Configuration (`config.yml`)

- **`authtoken`**: Your ngrok agent authtoken. The engine automatically generates a Termux/Linux-compatible runtime config (`logs/ngrok.yml`) with public DNS resolvers (`8.8.8.8`, `1.1.1.1`) so DNS resolution works seamlessly inside Android Termux as well as standard Linux.
- **`tunnel_domain`**: Your static ngrok domain (e.g., `example.ngrok-free.dev`). If omitted, the status view auto-discovers any active dynamic ngrok URL via the local ngrok API.
- **`MCP_TOKEN`**: Secret token used to authenticate MCP requests. Automatically generated during `./install.sh` or `./start.sh` if left blank or set to the placeholder value.
- **`HOST` / `PORT`**: Local bind address and port (default `127.0.0.1:8765`).
- **`TUNNEL_CMD`**: Leave empty (`""`) for automatic ngrok command generation using `authtoken`, `tunnel_domain`, `HOST`, and `PORT`. Set a custom command if using a different tunnel provider, or `"none"` to disable local tunnel launch.

## Security

- `config.yml`, runtime logs, backups, and state files are gitignored. Never commit your live `config.yml`.
- Treat the full MCP Connector URL (`https://<domain>/mcp/<MCP_TOKEN>`) as a secret credential.
