#!/usr/bin/env python3
"""Bounded decision, process supervisor, recovery, and config engine for Server Connector."""
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BIN_DIR = ROOT / "bin"
LOCAL_NGROK = BIN_DIR / "ngrok"
NGROK_RUNTIME_YML = BIN_DIR / "ngrok.yml"
CONFIG_PATH = ROOT / "config.yml"
CONFIG_EXAMPLE_PATH = ROOT / "config.example.yml"
LOGS = ROOT / "logs"
LOG_FILE = LOGS / "connector.log"
STATE = ROOT / "state.json"
PID_FILE = ROOT / "supervisor.pid"
POLL_SECONDS = 60
MAX_LOG_BYTES = 250_000


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_log(level, component, message):
    LOGS.mkdir(exist_ok=True)
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            data = LOG_FILE.read_bytes()[-int(MAX_LOG_BYTES * 0.7):]
            nl = data.find(b"\n")
            LOG_FILE.write_bytes(data[nl + 1:] if nl != -1 else data)
    except Exception:
        pass
    line = f"{now()} [{level:<5}] [{component:<10}] {message}\n"
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line)


def find_ngrok_bin():
    sys_ngrok = shutil.which("ngrok")
    if sys_ngrok:
        return sys_ngrok
    if LOCAL_NGROK.is_file() and os.access(LOCAL_NGROK, os.X_OK):
        return str(LOCAL_NGROK)
    return ""


def ensure_ngrok_binary(verbose=False):
    existing = find_ngrok_bin()
    if existing:
        return existing
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    elif machine.startswith("arm"):
        arch = "arm"
    else:
        return ""
    url = f"https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-{arch}.tgz"
    BIN_DIR.mkdir(exist_ok=True)
    tgz_path = BIN_DIR / "ngrok.tgz"
    if verbose:
        print(f"Downloading standalone ngrok ({arch}) into {LOCAL_NGROK} ...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "server-connector/2.3.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, tgz_path.open("wb") as out:
            shutil.copyfileobj(resp, out)
        with tarfile.open(tgz_path, "r:gz") as tar:
            src = tar.extractfile("ngrok")
            if src is None:
                raise RuntimeError("ngrok binary not found in archive")
            with LOCAL_NGROK.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        os.chmod(LOCAL_NGROK, 0o755)
        tgz_path.unlink(missing_ok=True)
        write_log("INFO", "INSTALL", f"installed standalone ngrok ({arch}) -> {LOCAL_NGROK}")
        if verbose:
            print("ngrok binary installed successfully.")
        return str(LOCAL_NGROK)
    except Exception as e:
        tgz_path.unlink(missing_ok=True)
        write_log("WARN", "INSTALL", f"automatic ngrok download failed: {e}")
        if verbose:
            print(f"WARNING: automatic ngrok download failed: {e}", file=sys.stderr)
        return ""


def load_config(path=None):
    cfg = {}
    p = Path(path) if path else CONFIG_PATH
    if p.is_file():
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            if not k or k.startswith("-"):
                continue
            if v and v[0] in ("'", '"'):
                end = v.find(v[0], 1)
                v = v[1:end] if end != -1 else v[1:]
            elif "#" in v:
                v = v.split("#", 1)[0].strip()
            cfg[k] = cfg[k.upper()] = cfg[k.lower()] = v
    return cfg


def normalize_domain(domain):
    d = (domain or "").strip()
    d = re.sub(r"^https?://", "", d, flags=re.IGNORECASE)
    return d.split("/", 1)[0].strip()


def discover_ngrok_domain():
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1.5) as r:
            data = json.loads(r.read(16384).decode("utf-8", "replace"))
            for t in data.get("tunnels") or []:
                pub = t.get("public_url") or ""
                if pub.startswith("https://"):
                    return normalize_domain(pub)
            for t in data.get("tunnels") or []:
                pub = t.get("public_url") or ""
                if pub:
                    return normalize_domain(pub)
    except Exception:
        pass
    return ""


def sync_ngrok_yml(authtoken):
    BIN_DIR.mkdir(exist_ok=True)
    if not authtoken:
        return
    content = (
        'version: "3"\n'
        "agent:\n"
        f'  authtoken: "{authtoken}"\n'
        "  dns_resolver_ips:\n"
        "    - 8.8.8.8\n"
        "    - 1.1.1.1\n"
    )
    NGROK_RUNTIME_YML.write_text(content, encoding="utf-8")
    try:
        os.chmod(NGROK_RUNTIME_YML, 0o600)
    except OSError:
        pass


def init_config(verbose=False):
    LOGS.mkdir(exist_ok=True)
    BIN_DIR.mkdir(exist_ok=True)
    (ROOT / "self_engine_state.json").unlink(missing_ok=True)
    (LOGS / "ngrok.yml").unlink(missing_ok=True)

    created = False
    if not CONFIG_PATH.is_file():
        if CONFIG_EXAMPLE_PATH.is_file():
            CONFIG_PATH.write_text(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            CONFIG_PATH.write_text(
                '# Server Connector Configuration (Single Source of Truth)\n'
                'authtoken: ""\n'
                'tunnel_domain: ""\n'
                'MCP_TOKEN: "REPLACE_WITH_A_LONG_RANDOM_SECRET"\n'
                'HOST: "127.0.0.1"\n'
                'PORT: 8765\n'
                'TUNNEL_CMD: ""\n',
                encoding="utf-8",
            )
        created = True

    cfg = load_config()
    token = cfg.get("MCP_TOKEN", "").strip()
    token_generated = False
    if not token or token == "REPLACE_WITH_A_LONG_RANDOM_SECRET":
        token = secrets.token_hex(24)
        text = CONFIG_PATH.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?m)^\s*MCP_TOKEN\s*:", text):
            text = re.sub(
                r"(?m)^(\s*MCP_TOKEN\s*:).*$",
                f'\\1 "{token}"',
                text,
                count=1,
            )
        else:
            text = text.rstrip() + f'\nMCP_TOKEN: "{token}"\n'
        CONFIG_PATH.write_text(text, encoding="utf-8")
        cfg = load_config()
        token_generated = True

    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass

    authtoken = cfg.get("AUTHTOKEN", "").strip()
    sync_ngrok_yml(authtoken)
    if authtoken:
        ensure_ngrok_binary(verbose=verbose)

    if verbose:
        if created or token_generated:
            print("Configured config.yml with a unique MCP_TOKEN. Keep it private.")
        else:
            print("config.yml verified and synced.")
    return cfg


def resolve_tunnel_cmd(cfg):
    raw_cmd = cfg.get("TUNNEL_CMD", "").strip()
    if raw_cmd.lower() in ("none", "off", "false", "disable", "disabled"):
        return ""
    authtoken = cfg.get("AUTHTOKEN", "").strip()
    domain = normalize_domain(cfg.get("TUNNEL_DOMAIN", ""))
    host = cfg.get("HOST", "").strip() or "127.0.0.1"
    port = cfg.get("PORT", "").strip() or "8765"
    ngrok_cfg = str(NGROK_RUNTIME_YML)
    ngrok_bin = find_ngrok_bin() or "ngrok"

    if not raw_cmd or raw_cmd.lower() == "auto":
        if not authtoken:
            return ""
        cmd = f'"{ngrok_bin}" http --config "{ngrok_cfg}"'
        if domain:
            cmd += f' --url="https://{domain}"'
        cmd += f" {host}:{port}"
        return cmd

    cmd = (
        raw_cmd.replace("{PORT}", port)
        .replace("{HOST}", host)
        .replace("{tunnel_domain}", domain)
        .replace("{authtoken}", authtoken)
        .replace("{ngrok_config}", ngrok_cfg)
    )
    if re.match(r"^ngrok\s+http\b", cmd):
        extra = ""
        if "--config" not in cmd and authtoken:
            extra += f' --config "{ngrok_cfg}"'
        if domain and not any(f in cmd for f in ("--domain", "--url", "--hostname")):
            extra += f' --url="https://{domain}"'
        cmd = re.sub(r"^ngrok(\s+http)\b", f'"{ngrok_bin}"\\1' + extra, cmd, count=1)
    return cmd


def get_effective_settings():
    cfg = load_config()
    host = os.environ.get("HOST") or cfg.get("HOST") or "127.0.0.1"
    port = os.environ.get("PORT") or cfg.get("PORT") or "8765"
    token = os.environ.get("MCP_TOKEN") or cfg.get("MCP_TOKEN") or ""
    domain = normalize_domain(os.environ.get("TUNNEL_DOMAIN") or cfg.get("TUNNEL_DOMAIN") or "")
    authtoken = os.environ.get("AUTHTOKEN") or cfg.get("AUTHTOKEN") or ""
    tunnel_cmd = resolve_tunnel_cmd(cfg)
    return {
        "HOST": host,
        "PORT": str(port),
        "MCP_TOKEN": token,
        "TUNNEL_DOMAIN": domain,
        "AUTHTOKEN": authtoken,
        "TUNNEL_CMD": tunnel_cmd,
    }


def write_json_atomic(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def check_url_health(url, timeout=3):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "server-connector/2.3.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(4096).decode("utf-8", "replace"))
            return {
                "ok": r.status == 200 and data.get("ok") is True,
                "status": r.status,
                "version": data.get("version"),
            }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)[:160]}


def health():
    s = get_effective_settings()
    url = f"http://127.0.0.1:{s['PORT']}/health"
    return check_url_health(url, timeout=3)


def list_all_processes():
    rows = []
    proc_dir = Path("/proc")
    if proc_dir.is_dir():
        for entry in proc_dir.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                raw = (entry / "cmdline").read_bytes()
                if not raw:
                    continue
                cmd = " ".join(part.decode("utf-8", "replace") for part in raw.rstrip(b"\x00").split(b"\x00"))
                rows.append((pid, cmd))
            except Exception:
                continue
        if rows:
            return rows
    try:
        p = subprocess.run(["ps", "-A", "-o", "pid,args"], capture_output=True, text=True, timeout=5)
        for line in p.stdout.splitlines()[1:]:
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                rows.append((int(parts[0]), parts[1]))
    except Exception:
        pass
    return rows


def find_connector_pids():
    my_pid = os.getpid()
    port = get_effective_settings()["PORT"]
    supervisors = []
    workers = []
    for pid, cmd in list_all_processes():
        if pid == my_pid:
            continue
        if any(p in cmd for p in ("supervisor.sh", "engine.py daemon", "self_engine.py daemon")):
            supervisors.append((pid, cmd))
        elif re.search(r"(^|[\s/])python3?\s+.*server\.py\b", cmd):
            workers.append((pid, cmd))
        elif "ngrok" in cmd and ("ngrok.yml" in cmd or f":{port}" in cmd or f" {port}" in cmd):
            workers.append((pid, cmd))
    return supervisors, workers


def stop_all_processes():
    if PID_FILE.is_file():
        try:
            sup_pid = int(PID_FILE.read_text().strip())
            os.kill(sup_pid, signal.SIGTERM)
        except Exception:
            pass
    supervisors, workers = find_connector_pids()
    for pid, _ in supervisors:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(0.3)
    _, workers = find_connector_pids()
    all_targets = [pid for pid, _ in supervisors] + [pid for pid, _ in workers]
    for pid in all_targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    deadline = time.time() + 2.0
    while time.time() < deadline:
        s_rem, w_rem = find_connector_pids()
        if not s_rem and not w_rem:
            break
        time.sleep(0.2)

    s_rem, w_rem = find_connector_pids()
    for pid, _ in s_rem + w_rem:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    PID_FILE.unlink(missing_ok=True)
    (ROOT / "self_engine_state.json").unlink(missing_ok=True)
    if all_targets:
        write_log("INFO", "SUPERVISOR", f"stopped pids={sorted(set(all_targets))}")


def processes():
    supervisors, workers = find_connector_pids()
    my_pid = os.getpid()
    out = []
    for pid, cmd in supervisors + workers:
        if pid != my_pid:
            out.append(f"{pid} {cmd}")
    return out[:12]


def tail(path, limit=12000):
    try:
        if not path.exists():
            return ""
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size - limit))
            return f.read(limit).decode("utf-8", "replace")
    except Exception:
        return ""


def decide(log_always=False):
    (ROOT / "self_engine_state.json").unlink(missing_ok=True)
    h = health()
    proc = processes()
    connector_log = tail(LOG_FILE)
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text())
        except Exception:
            state = {}
    errors = {
        "address_in_use": connector_log.count("Address already in use"),
        "tracebacks": connector_log.count("Traceback (most recent call last):"),
        "upgrade_errors": connector_log.count("[UPGRADE   ] ERROR:"),
    }
    candidate = ROOT / "server.py.candidate"
    actions = []
    if not h["ok"]:
        actions.append({
            "action": "wait_and_recheck",
            "reason": "health endpoint unavailable; supervisor is responsible for restarting server",
        })
        decision = "SERVICE_UNHEALTHY_MONITOR_SUPERVISOR"
    elif "address_in_use" in state and errors["address_in_use"] > int(state.get("address_in_use", 0)):
        actions.append({
            "action": "report_supervisor_duplication",
            "reason": "new address-in-use errors observed; no process will be killed automatically",
        })
        decision = "INVESTIGATE_DUPLICATE_SERVER_STARTS"
    elif candidate.exists():
        actions.append({
            "action": "validate_candidate",
            "reason": "candidate detected; never install unvalidated code",
        })
        decision = "CANDIDATE_REQUIRES_VALIDATION"
    else:
        actions.append({
            "action": "no_change",
            "reason": "health is good and no staged upgrade exists",
        })
        decision = "HEALTHY_NO_CHANGE"

    prev_decision = state.get("last_decision")
    state.update({
        "last_check": now(),
        "last_decision": decision,
        "address_in_use": errors["address_in_use"],
        "tracebacks": errors["tracebacks"],
        "upgrade_errors": errors["upgrade_errors"],
    })
    write_json_atomic(STATE, state)

    if log_always or decision != prev_decision or decision != "HEALTHY_NO_CHANGE":
        lvl = "INFO" if h["ok"] else "WARN"
        write_log(
            lvl,
            "ENGINE",
            f"decision={decision} health_ok={h['ok']} tracebacks={errors['tracebacks']} addr_in_use={errors['address_in_use']}",
        )

    return {
        "decision": decision,
        "health": h,
        "errors": errors,
        "processes": proc,
        "actions": actions,
        "candidate_present": candidate.exists(),
        "policy": "bounded_actions; no arbitrary self-modification; backups and validation required",
        "time": now(),
    }


def daemon():
    init_config(verbose=False)
    s = get_effective_settings()
    PID_FILE.write_text(f"{os.getpid()}\n", encoding="utf-8")
    write_log(
        "INFO",
        "SUPERVISOR",
        f"started pid={os.getpid()} host={s['HOST']} port={s['PORT']} domain={s['TUNNEL_DOMAIN'] or 'none'}",
    )
    if s["TUNNEL_DOMAIN"]:
        write_log("INFO", "TUNNEL", f"endpoint=https://{s['TUNNEL_DOMAIN']}/mcp/{s['MCP_TOKEN']}")

    server_proc = None
    tunnel_proc = None
    stopping = False

    def terminate_child(proc):
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def handle_sig(signum, frame):
        nonlocal stopping
        stopping = True
        terminate_child(tunnel_proc)
        terminate_child(server_proc)
        PID_FILE.unlink(missing_ok=True)
        write_log("INFO", "SUPERVISOR", f"stopped pid={os.getpid()}")
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle_sig)
    signal.signal(signal.SIGINT, handle_sig)

    tunnel_args = None
    if s["TUNNEL_CMD"]:
        parsed = shlex.split(s["TUNNEL_CMD"])
        if parsed and (shutil.which(parsed[0]) or Path(parsed[0]).is_file()):
            tunnel_args = parsed
        else:
            write_log("WARN", "TUNNEL", f"binary '{parsed[0] if parsed else ''}' not found; skipping local tunnel")

    last_health_check = 0.0
    unhealthy_streak = 0
    first_check = True

    while not stopping:
        if server_proc is None or server_proc.poll() is not None:
            if server_proc is not None:
                write_log("WARN", "SUPERVISOR", f"service=server exited rc={server_proc.returncode}; restarting")
            server_proc = subprocess.Popen(
                [sys.executable, str(ROOT / "server.py")],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        if tunnel_args is not None and (tunnel_proc is None or tunnel_proc.poll() is not None):
            if tunnel_proc is not None:
                write_log("WARN", "SUPERVISOR", f"service=tunnel exited rc={tunnel_proc.returncode}; restarting")
            tunnel_proc = subprocess.Popen(
                tunnel_args,
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        now_ts = time.time()
        if now_ts - last_health_check >= POLL_SECONDS or (first_check and now_ts - last_health_check >= 2.0):
            last_health_check = now_ts
            try:
                report = decide(log_always=first_check)
                first_check = False
                if report.get("health", {}).get("ok"):
                    unhealthy_streak = 0
                else:
                    unhealthy_streak += 1
                    if unhealthy_streak >= 2 and server_proc is not None:
                        write_log("WARN", "ENGINE", f"decision=SERVER_RESTART_REQUESTED pid={server_proc.pid}")
                        terminate_child(server_proc)
                        server_proc = None
                        unhealthy_streak = 0
            except Exception as e:
                write_log("ERROR", "ENGINE", f"exception={type(e).__name__}: {str(e)[:200]}")

        time.sleep(2)


def print_env():
    init_config(verbose=False)
    s = get_effective_settings()
    for k, v in s.items():
        print(f"export {k}={shlex.quote(str(v))}")


def format_status_banner(check_remote=True):
    s = get_effective_settings()
    host = s["HOST"]
    port = s["PORT"]
    token = s["MCP_TOKEN"]
    domain = s["TUNNEL_DOMAIN"] or discover_ngrok_domain()
    local_base = f"http://{host}:{port}"
    local_health_url = f"{local_base}/health"
    local_mcp_url = f"{local_base}/mcp"

    lines = [
        "",
        "==============================================================",
        "                 SERVER CONNECTOR LIVE STATUS                 ",
        "==============================================================",
        f"Config Source           : {CONFIG_PATH}",
        f"Structured Log          : {LOG_FILE}",
        f"Local Health URL (GET)  : {local_health_url}",
        f"Local MCP URL (POST)    : {local_mcp_url}/{token}",
    ]

    if domain:
        pub_base = f"https://{domain}"
        pub_health = f"{pub_base}/health"
        pub_mcp = f"{pub_base}/mcp/{token}"
        pub_alt = f"{pub_base}/mcp"
        lines.extend([
            "--------------------------------------------------------------",
            f"Tunnel Domain           : {domain}",
            f"Public Base URL         : {pub_base}",
            f"Public Health URL (GET) : {pub_health}",
            f"MCP Connector URL (POST): {pub_mcp}",
            f"Alt MCP Endpoint (POST) : {pub_alt}",
            f"Alt Auth Header         : Authorization: Bearer {token}",
        ])
    else:
        lines.extend([
            "--------------------------------------------------------------",
            "Tunnel Domain           : (not configured in config.yml)",
        ])

    lines.append("--------------------------------------------------------------")
    lh = check_url_health(f"http://127.0.0.1:{port}/health", timeout=2)
    if lh["ok"]:
        lines.append(f"[Local Server]  ONLINE      (v{lh.get('version', '2.3.0')} on {host}:{port})")
    else:
        lines.append(f"[Local Server]  OFFLINE     ({lh.get('error', 'unreachable')})")

    if domain and check_remote:
        rh = check_url_health(f"https://{domain}/health", timeout=5)
        if rh["ok"]:
            lines.append(f"[Remote Tunnel] ONLINE      (https://{domain}/health)")
        else:
            lines.append(f"[Remote Tunnel] UNREACHABLE ({rh.get('error', 'check tunnel process')})")
    lines.append("==============================================================")
    return "\n".join(lines)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "check":
        print(json.dumps(decide(log_always=False), indent=2, ensure_ascii=False))
        return 0
    if mode == "daemon":
        daemon()
        return 0
    if mode == "stop":
        stop_all_processes()
        return 0
    if mode == "init-config":
        init_config(verbose=True)
        return 0
    if mode == "env":
        print_env()
        return 0
    if mode == "status":
        print(format_status_banner(check_remote=True))
        return 0
    if mode == "log":
        lvl = sys.argv[2] if len(sys.argv) > 2 else "INFO"
        comp = sys.argv[3] if len(sys.argv) > 3 else "SUPERVISOR"
        msg = " ".join(sys.argv[4:]) if len(sys.argv) > 4 else ""
        if msg:
            write_log(lvl, comp, msg)
        return 0
    print("usage: engine.py check|daemon|stop|init-config|env|status|log")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
