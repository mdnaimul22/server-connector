# doomsday-connector

Minimal MCP server over HTTP to give ChatGPT, Claude, Antigravity, or any remote MCP client direct shell and file access to an Android phone (Termux), a Linux machine, or a Windows machine (via Git for Windows).

Built specifically with the Python standard library only. No `pip install`, no virtualenvs, and no C-extension wheel builds that break on Termux.

## Why it works this way

- **Stdlib only:** Runs out of the box on stock Python 3 (`http.server`, `subprocess`, `urllib`).
- **Single config (`config.yml`):** Everything lives in one file. No `.env` quoting issues or scattered tunnel configs.
- **Auto tunnel setup:** If `ngrok` isn't installed, `./start.sh` installs the right static binary per platform — `.tgz` on Termux/Linux, `.zip` on Windows/macOS — into `./bin/ngrok`, generates a runtime config with public DNS resolvers (`8.8.8.8`, `1.1.1.1`), and binds `/etc/resolv.conf` through `proot` on Termux so DNS never fails. The reserved-domain flag is detected from the installed agent (`--url` on modern ngrok, `--domain` on older ones), and `./bin/ngrok` always takes priority over a system copy.
- **Same shell everywhere:** `run_command` and `start_job` always execute through a POSIX shell. On Windows that is Git Bash, so `grep`, `sed`, `awk`, `find`, pipes, `&&`, `$VARS`, and heredocs behave exactly like on Linux instead of hitting `cmd.exe` limitations.

## Windows notes

Requirements: **Python 3** and **Git for Windows** (for `bash`, `chmod`, and the coreutils). Install both, then run the setup from Git Bash:

```sh
git clone https://github.com/mdnaimul22/server-connector.git
cd server-connector
chmod +x *.sh
./install.sh
./start.sh
```

- Commands given to `run_command` are evaluated by `bash -c`, so use Unix syntax. Windows executables still work: `cmd //c dir` (the doubled slash stops MSYS from rewriting `/c` to a path), `powershell -NoProfile -Command '...'` (single quotes, otherwise bash expands `$variables` first), `notepad.exe`, etc.
- Paths can be either `C:/Users/you/file` or `/c/Users/you/file`.
- Output is decoded as UTF-8 on every platform, so non-ASCII text (and stray binary bytes) no longer turns into `None` or raises decode errors.
- Set `SHELL_BIN` in `config.yml` to override the shell, or to `"cmd"` to force classic `cmd.exe`.
- `install.sh`, `start.sh`, and `stop.sh` ignore the Microsoft Store `python3` alias (it exits non-zero) and use the real interpreter from `%LOCALAPPDATA%\Programs\Python`.
- Background jobs are launched through a small Python runner, so they survive server restarts and report their exit code and duration on both Windows and POSIX.
- Nothing flashes a console window. The logon task runs `pythonw.exe` (GUI subsystem, so Task Scheduler never opens a console) and every child process — `bash`, `powershell`, `taskkill`, `ngrok`, the server and background jobs — is started with `CREATE_NO_WINDOW`. Verified with a window watcher across autostart, `run_command`, `start_job`, and the supervisor's 60-second health cycle.

## Setup

```sh
git clone https://github.com/mdnaimul22/server-connector.git
cd server-connector
chmod +x *.sh
./install.sh
```

`install.sh` detects the platform once and then follows an explicit branch:

| Platform | Installer behavior |
|---|---|
| Termux | `pkg install python curl proot dnsutils` |
| Linux | `apt-get`, `dnf`, or `pacman` (through `sudo` when not root) |
| macOS | `brew install python3` when Homebrew exists, otherwise prints the download link |
| Windows | verifies Python 3 and Git Bash, and prints the exact `winget` commands if either is missing |

On Windows run everything from **Git Bash** (installed with Git for Windows), not PowerShell.

Open `config.yml` and drop in your ngrok `authtoken` and static `tunnel_domain`:

```yaml
authtoken: "your_ngrok_authtoken"
tunnel_domain: "your-domain.ngrok-free.dev"
MCP_TOKEN: "auto-generated-or-your-own-secret"
HOST: "127.0.0.1"
PORT: 8765
TUNNEL_CMD: ""
SHELL_BIN: ""
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

Paste the **MCP Connector URL** into ChatGPT, Claude, or Antigravity MCP settings and you're connected.

## Commands

- `./start.sh` — start (or restart after editing `config.yml`) and print status
- `./status.sh` — check live local + remote tunnel health and print URLs
- `./stop.sh` — stop supervisor, server, engine, and tunnel processes
- `./autostart.sh install|remove|status` — manage start-at-logon (see below)
- `./install.sh [--autostart]` — set up Python/ngrok and optionally register autostart
- `./upgrade.py check | apply` — validate and hot-swap `server.py.candidate` with automatic rollback if health checks fail

## Auto-start when the machine starts

```sh
./autostart.sh install
./autostart.sh status
./autostart.sh remove
```

Each platform gets its own mechanism:

| Platform | Mechanism |
|---|---|
| Windows | Task Scheduler task `DoomsdayConnector`: runs `pythonw.exe engine.py spawn` 10 seconds after you log on, which launches `start.sh` with no console window |
| Termux | `~/.termux/boot/doomsday-connector.sh` (requires the Termux:Boot app) |
| Linux | systemd user unit `doomsday-connector.service` (`systemctl --user`) |
| macOS | LaunchAgent `dev.doomsday.connector.plist` |

`./install.sh --autostart` registers it during install. On Windows the task runs at
**logon**, with your normal user rights, so no admin or service installation is
needed. Starting before anyone logs in would require an elevated `AtStartup` task
or a real Windows service.

## Running on multiple machines

Free ngrok accounts allow 1 static domain and 1 active agent session per account. To run this across multiple devices (e.g., Termux on phone + Linux desktop + VPS) at the same time:

1. Use a separate free ngrok account (`authtoken` + `tunnel_domain`) per machine, or
2. Set `TUNNEL_CMD` in `config.yml` to your own tunnel command (e.g., `cloudflared`, `ssh -R`), or `"none"` if you're reverse-proxying it yourself.

## Files

```text
config.yml          local config & secrets (gitignored)
config.example.yml  template config
server.py           MCP JSON-RPC server & tools
engine.py           config loader, process supervisor, health watchdog, autostart
upgrade.py          safe atomic updater with rollback
python_env.sh       shared real-Python detection (skips Store shims)
start.sh / stop.sh  service control
status.sh           terminal status dashboard
autostart.sh        start-at-logon management (per platform)
```
