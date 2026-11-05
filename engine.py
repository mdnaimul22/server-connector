#!/usr/bin/env python3
"""Bounded decision, process supervisor, recovery, and config engine for Doomsday Connector."""
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
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WIN = os.name == "nt"
BIN_DIR = ROOT / "bin"
LOCAL_NGROK = BIN_DIR / ("ngrok.exe" if IS_WIN else "ngrok")
NGROK_RUNTIME_YML = BIN_DIR / "ngrok.yml"
NGROK_LOG_FILE = LOGS = ROOT / "logs"
LOG_FILE = LOGS / "connector.log"
TUNNEL_DIAG_FILE = BIN_DIR / "ngrok.last.log"
STATE = ROOT / "state.json"
PID_FILE = ROOT / "supervisor.pid"
CONFIG_PATH = ROOT / "config.yml"
CONFIG_EXAMPLE_PATH = ROOT / "config.example.yml"
TERMUX_RESOLV = Path("/data/data/com.termux/files/usr/etc/resolv.conf")
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


NGROK_BASE_URL = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-{os}-{arch}.{ext}"
_NGROK_FLAG_CACHE = {}


def platform_kind():
    """Classify the host so every platform gets its own install branch."""
    if IS_WIN:
        return "windows"
    if Path("/data/data/com.termux/files/usr").is_dir():
        return "termux"
    system = platform.system().lower()
    if system in ("linux", "darwin"):
        return system
    return system or "unknown"


def ngrok_arch():
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64", "x64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    if machine.startswith("arm"):
        return "arm"
    return ""


def _ngrok_candidates_windows():
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    candidates = [
        local / "Microsoft" / "WinGet" / "Links" / "ngrok.exe",
        local / "ngrok" / "ngrok.exe",
        program_files / "ngrok" / "ngrok.exe",
        program_files_x86 / "ngrok" / "ngrok.exe",
    ]
    # winget sometimes extracts the package without creating the Links shim, so
    # look inside the package folders as well.
    packages = local / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        for package_dir in sorted(packages.glob("Ngrok*")):
            candidates.extend(sorted(package_dir.rglob("ngrok.exe")))
    return candidates


def _ngrok_candidates_posix():
    return [
        Path("/usr/local/bin/ngrok"),
        Path("/usr/bin/ngrok"),
        Path("/snap/bin/ngrok"),
        Path("/opt/homebrew/bin/ngrok"),
        Path("/usr/local/opt/ngrok/bin/ngrok"),
        Path.home() / "ngrok" / "ngrok",
        Path.home() / "bin" / "ngrok",
    ]


def find_ngrok_bin():
    # The project-managed binary wins: it is the one an install/update would refresh.
    try:
        if LOCAL_NGROK.is_file():
            return str(LOCAL_NGROK)
    except OSError:
        pass
    found = shutil.which("ngrok")
    if found:
        return found
    candidates = _ngrok_candidates_windows() if IS_WIN else _ngrok_candidates_posix()
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return ""


def _ngrok_version(binary):
    try:
        proc = subprocess.run([binary, "version"], capture_output=True, text=True, timeout=20,
                              creationflags=no_window_flags())
    except Exception:
        return ""
    match = re.search(r"ngrok version\s+([0-9][^\s]*)", (proc.stdout or "") + (proc.stderr or ""), re.IGNORECASE)
    return match.group(1) if match else ""


def _download(url, destination):
    req = urllib.request.Request(url, headers={"User-Agent": "doomsday-connector/2.3.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, destination.open("wb") as out:
        shutil.copyfileobj(resp, out)


def _extract_zip(archive, destination):
    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist() if Path(n).name in ("ngrok.exe", "ngrok")]
        if not names:
            raise RuntimeError("ngrok binary not found in archive")
        with zf.open(names[0]) as src, destination.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def _extract_tgz(archive, destination):
    with tarfile.open(archive, "r:gz") as tar:
        member = next((m for m in tar.getmembers() if Path(m.name).name == "ngrok" and m.isfile()), None)
        if member is None:
            raise RuntimeError("ngrok binary not found in archive")
        src = tar.extractfile(member)
        with destination.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def _install_ngrok_archive(os_name, arch, ext, verbose=False, hint=""):
    """Download and unpack the standalone ngrok build for one platform."""
    url = NGROK_BASE_URL.format(os=os_name, arch=arch, ext=ext)
    label = f"{os_name}-{arch}"
    BIN_DIR.mkdir(exist_ok=True)
    archive_path = BIN_DIR / f"ngrok.{ext}"
    if verbose:
        print(f"Downloading standalone ngrok ({label}) into {LOCAL_NGROK} ...")
    try:
        _download(url, archive_path)
        if ext == "zip":
            _extract_zip(archive_path, LOCAL_NGROK)
        else:
            _extract_tgz(archive_path, LOCAL_NGROK)
        try:
            os.chmod(LOCAL_NGROK, 0o755)
        except OSError:
            pass
        archive_path.unlink(missing_ok=True)
        write_log("INFO", "INSTALL", f"installed standalone ngrok ({label}) -> {LOCAL_NGROK}")
        if verbose:
            print("ngrok binary installed successfully.")
        return str(LOCAL_NGROK)
    except Exception as e:
        archive_path.unlink(missing_ok=True)
        message = f"automatic ngrok download failed ({label}): {e}"
        if hint:
            message += f" | {hint}"
        write_log("WARN", "INSTALL", message)
        if verbose:
            print(f"WARNING: {message}", file=sys.stderr)
        return ""


def _ensure_ngrok_windows(arch, verbose=False):
    # 1) Direct zip download first: winget's Ngrok.Ngrok package can be stale
    #    (3.3.1 at the time of writing), and ngrok rejects agents older than 3.28.
    result = _install_ngrok_archive(
        "windows", arch, "zip", verbose,
        hint=("Windows Defender may have blocked the download; add an exclusion for "
              f"{BIN_DIR} or install ngrok manually from https://ngrok.com/download"),
    )
    if result:
        return result

    # 2) Official winget package as a fallback when the download is blocked.
    if shutil.which("winget"):
        if verbose:
            print("Falling back to winget (Ngrok.Ngrok) ...")
        try:
            proc = subprocess.run(
                [
                    "winget", "install", "--id", "Ngrok.Ngrok", "-e", "--source", "winget",
                    "--accept-package-agreements", "--accept-source-agreements",
                    "--disable-interactivity",
                ],
                capture_output=True, text=True, timeout=600,
                creationflags=no_window_flags(),
            )
            found = find_ngrok_bin()
            if found:
                version = _ngrok_version(found) or "unknown"
                try:
                    shutil.copy2(found, LOCAL_NGROK)
                    target = str(LOCAL_NGROK)
                except Exception as e:
                    write_log("WARN", "INSTALL", f"could not copy winget ngrok into {LOCAL_NGROK}: {e}")
                    target = found
                if verbose:
                    print(f"ngrok installed via winget: {target} (v{version})")
                write_log("INFO", "INSTALL", f"installed ngrok via winget -> {target} (v{version})")
                return target
            output = (proc.stdout or proc.stderr or "").strip().splitlines()
            detail = output[-1] if output else f"rc={proc.returncode}"
            write_log("WARN", "INSTALL", f"winget did not provide ngrok: {detail[:160]}")
        except Exception as e:
            write_log("WARN", "INSTALL", f"winget install failed: {type(e).__name__}: {e}")
    return ""


def _ensure_ngrok_termux(arch, verbose=False):
    return _install_ngrok_archive(
        "linux", arch, "tgz", verbose,
        hint=("if DNS fails on Termux, bind the host resolv.conf with proot "
              "or set TUNNEL_CMD manually"),
    )


def _ensure_ngrok_linux(arch, verbose=False):
    return _install_ngrok_archive(
        "linux", arch, "tgz", verbose,
        hint=("install ngrok with your package manager (for example "
              "'snap install ngrok') or place the binary in ./bin/ngrok"),
    )


def _ensure_ngrok_darwin(arch, verbose=False):
    return _install_ngrok_archive(
        "darwin", arch, "zip", verbose,
        hint="or install ngrok with 'brew install ngrok/ngrok/ngrok'",
    )


def ensure_ngrok_binary(verbose=False):
    """Ensure the project-managed ngrok exists, installing one per platform if needed."""
    try:
        if LOCAL_NGROK.is_file():
            return str(LOCAL_NGROK)
    except OSError:
        pass

    kind = platform_kind()
    arch = ngrok_arch()
    if not arch:
        message = f"unsupported CPU architecture for automatic ngrok install: {platform.machine()}"
        write_log("WARN", "INSTALL", message)
        if verbose:
            print(f"WARNING: {message}", file=sys.stderr)
        return ""

    if kind == "windows":
        return _ensure_ngrok_windows(arch, verbose)
    if kind == "termux":
        return _ensure_ngrok_termux(arch, verbose)
    if kind == "linux":
        return _ensure_ngrok_linux(arch, verbose)
    if kind == "darwin":
        return _ensure_ngrok_darwin(arch, verbose)

    message = f"automatic ngrok install is not implemented for platform '{kind}'"
    write_log("WARN", "INSTALL", message)
    if verbose:
        print(f"WARNING: {message}", file=sys.stderr)
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
        'version: "2"\n'
        f'authtoken: "{authtoken}"\n'
        "dns_resolver_ips:\n"
        "  - 8.8.8.8\n"
        "  - 1.1.1.1\n"
    )
    NGROK_RUNTIME_YML.write_text(content, encoding="utf-8")
    try:
        os.chmod(NGROK_RUNTIME_YML, 0o600)
    except OSError:
        pass


def ensure_getent_shim():
    if IS_WIN or shutil.which("getent"):
        return
    BIN_DIR.mkdir(exist_ok=True)
    shim = BIN_DIR / "getent"
    script = (
        f"#!{sys.executable}\n"
        "import socket, sys\n"
        "args = sys.argv[1:]\n"
        "if not args:\n"
        "    sys.exit(1)\n"
        "db, keys = args[0], args[1:]\n"
        "if db in ('hosts', 'ahosts', 'ahostsv4', 'ahostsv6'):\n"
        "    if not keys:\n"
        "        sys.exit(0)\n"
        "    fam = socket.AF_INET if db == 'ahostsv4' else (socket.AF_INET6 if db == 'ahostsv6' else 0)\n"
        "    ok = False\n"
        "    for k in keys:\n"
        "        try:\n"
        "            infos = socket.getaddrinfo(k, None, fam, socket.SOCK_STREAM)\n"
        "            seen = []\n"
        "            for info in infos:\n"
        "                ip = info[4][0]\n"
        "                if ip not in seen:\n"
        "                    seen.append(ip)\n"
        "            for ip in seen:\n"
        "                if db == 'hosts':\n"
        "                    print(f'{ip:<15} {k}')\n"
        "                else:\n"
        "                    print(f'{ip:<15} STREAM {k}')\n"
        "                    print(f'{ip:<15} DGRAM  ')\n"
        "                    print(f'{ip:<15} RAW    ')\n"
        "            if seen:\n"
        "                ok = True\n"
        "        except Exception:\n"
        "            pass\n"
        "    sys.exit(0 if ok else 2)\n"
        "if db == 'passwd':\n"
        "    import pwd\n"
        "    entries = [pwd.getpwnam(k) if not k.isdigit() else pwd.getpwuid(int(k)) for k in keys] if keys else pwd.getpwall()\n"
        "    for e in entries:\n"
        "        print(f'{e.pw_name}:x:{e.pw_uid}:{e.pw_gid}:{e.pw_gecos}:{e.pw_dir}:{e.pw_shell}')\n"
        "    sys.exit(0)\n"
        "sys.exit(2)\n"
    )
    shim.write_text(script, encoding="utf-8")
    try:
        os.chmod(shim, 0o755)
    except OSError:
        pass


def init_config(verbose=False):
    LOGS.mkdir(exist_ok=True)
    BIN_DIR.mkdir(exist_ok=True)
    ensure_getent_shim()
    (ROOT / "self_engine_state.json").unlink(missing_ok=True)
    (LOGS / "ngrok.yml").unlink(missing_ok=True)

    created = False
    if not CONFIG_PATH.is_file():
        if CONFIG_EXAMPLE_PATH.is_file():
            CONFIG_PATH.write_text(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            CONFIG_PATH.write_text(
                '# Doomsday Connector Configuration (Single Source of Truth)\n'
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


def ngrok_domain_flag(binary):
    """Pick the reserved-domain flag the installed ngrok understands (--url vs --domain)."""
    if not binary:
        return "--url"
    cached = _NGROK_FLAG_CACHE.get(binary)
    if cached:
        return cached
    flag = ""
    try:
        probe = subprocess.run(
            [binary, "http", "--help"],
            capture_output=True, text=True, timeout=20,
            creationflags=no_window_flags(),
        )
        help_text = (probe.stdout or "") + (probe.stderr or "")
        if "--url" in help_text:
            flag = "--url"
        elif "--domain" in help_text:
            flag = "--domain"
    except Exception:
        pass
    flag = flag or "--url"
    _NGROK_FLAG_CACHE[binary] = flag
    return flag


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

    proot_prefix = ""
    if TERMUX_RESOLV.is_file() and shutil.which("proot"):
        proot_prefix = f"proot -b {TERMUX_RESOLV}:/etc/resolv.conf "

    if not raw_cmd or raw_cmd.lower() == "auto":
        if not authtoken:
            return ""
        cmd = f'{proot_prefix}"{ngrok_bin}" http --config "{ngrok_cfg}"'
        if domain:
            flag = ngrok_domain_flag(ngrok_bin)
            value = f"https://{domain}" if flag == "--url" else domain
            cmd += f' {flag}="{value}"'
        cmd += f" {host}:{port} --log=stdout --log-format=logfmt"
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
            flag = ngrok_domain_flag(ngrok_bin)
            value = f"https://{domain}" if flag == "--url" else domain
            extra += f' {flag}="{value}"'
        if "--log=" not in cmd:
            extra += " --log=stdout --log-format=logfmt"
        cmd = re.sub(r"^ngrok(\s+http)\b", f'{proot_prefix}"{ngrok_bin}"\\1' + extra, cmd, count=1)
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
        req = urllib.request.Request(url, headers={"User-Agent": "doomsday-connector/2.3.0"})
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
    if IS_WIN:
        try:
            p = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"],
                capture_output=True, text=True, timeout=30, creationflags=no_window_flags(),
            )
            for line in p.stdout.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2 and parts[0].isdigit():
                    rows.append((int(parts[0]), parts[1]))
        except Exception:
            pass
        return rows
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


def kill_process(pid, force=False):
    """Terminate a process (and its children on Windows) without touching other PIDs."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if IS_WIN:
        args = ["taskkill", "/T", "/PID", str(pid)]
        if force:
            args.insert(1, "/F")
        try:
            subprocess.run(args, capture_output=True, timeout=20, creationflags=no_window_flags())
        except Exception:
            pass
        return
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except OSError:
        pass


def stop_all_processes():
    if PID_FILE.is_file():
        try:
            kill_process(int(PID_FILE.read_text().strip()))
        except Exception:
            pass
    supervisors, workers = find_connector_pids()
    for pid, _ in supervisors:
        kill_process(pid)
    time.sleep(0.3)
    _, workers = find_connector_pids()
    all_targets = [pid for pid, _ in supervisors] + [pid for pid, _ in workers]
    for pid in all_targets:
        kill_process(pid)

    deadline = time.time() + (5.0 if IS_WIN else 2.0)
    while time.time() < deadline:
        s_rem, w_rem = find_connector_pids()
        if not s_rem and not w_rem:
            break
        time.sleep(0.2)

    s_rem, w_rem = find_connector_pids()
    for pid, _ in s_rem + w_rem:
        kill_process(pid, force=True)

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
    server_err_file = None
    tunnel_proc = None
    tunnel_err_file = None
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
        parsed = shlex.split(s["TUNNEL_CMD"], posix=not IS_WIN)
        if IS_WIN:
            # posix=False keeps quotes inside tokens ("--url=x", '"C:\\path\\ngrok.exe"'),
            # which Windows programs would receive literally.
            parsed = [re.sub(r'"([^"]*)"', r"\1", p) for p in parsed]
            parsed = [re.sub(r"'([^']*)'", r"\1", p) for p in parsed]
        else:
            parsed = [p[1:-1] if len(p) > 1 and p[0] == p[-1] == '"' else p for p in parsed]
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
                err_out = ""
                try:
                    if server_err_file:
                        server_err_file.seek(0)
                        lines = [ln.strip() for ln in server_err_file.read().splitlines() if ln.strip()]
                        err_out = lines[-1] if lines else ""
                        server_err_file.close()
                except Exception:
                    pass
                write_log(
                    "WARN",
                    "SUPERVISOR",
                    f"service=server exited rc={server_proc.returncode} err={err_out!r}; restarting",
                )
            server_err_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
            server_proc = subprocess.Popen(
                [sys.executable, str(ROOT / "server.py")],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=server_err_file,
                stderr=server_err_file,
                creationflags=no_window_flags(),
            )

        if tunnel_args is not None and (tunnel_proc is None or tunnel_proc.poll() is not None):
            if tunnel_proc is not None:
                err_out = ""
                try:
                    if TUNNEL_DIAG_FILE.is_file():
                        lines = [
                            ln.strip()
                            for ln in TUNNEL_DIAG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
                            if ln.strip() and ("lvl=eror" in ln or "ERROR" in ln or "err=" in ln) and "err=<nil>" not in ln
                        ]
                        err_out = lines[-1] if lines else ""
                    if tunnel_err_file:
                        tunnel_err_file.close()
                except Exception:
                    pass
                write_log(
                    "WARN",
                    "SUPERVISOR",
                    f"service=tunnel exited rc={tunnel_proc.returncode} err={err_out!r}; restarting",
                )
            BIN_DIR.mkdir(exist_ok=True)
            tunnel_err_file = TUNNEL_DIAG_FILE.open("w+", encoding="utf-8")
            tunnel_proc = subprocess.Popen(
                tunnel_args,
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=tunnel_err_file,
                stderr=tunnel_err_file,
                creationflags=no_window_flags(),
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
        "                 DOOMSDAY CONNECTOR LIVE STATUS               ",
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
            if TUNNEL_DIAG_FILE.is_file():
                diag = [
                    ln.strip()
                    for ln in TUNNEL_DIAG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
                    if ("lvl=eror" in ln or "ERROR" in ln or "err=" in ln) and "err=<nil>" not in ln and "hardware address" not in ln
                ]
                if diag:
                    lines.append(f"[Tunnel Error ] {diag[-1][-160:]}")
    lines.append("==============================================================")
    return "\n".join(lines)


AUTOSTART_WINDOWS_TASK = "DoomsdayConnector"


def _find_bash():
    # Prefer Git's wrapper over usr/bin/bash.exe: launched directly by Task
    # Scheduler, the raw binary cannot resolve its own /usr/bin PATH (exit 127).
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    for candidate in (
        program_files / "Git" / "bin" / "bash.exe",
        program_files / "Git" / "usr" / "bin" / "bash.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("bash") or ""


def _msys_path(path):
    text = str(path).replace("\\", "/")
    if len(text) > 2 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def no_window_flags():
    """Avoid flashing console windows when the parent has no console."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0


def _run_powershell(script, timeout=180):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=timeout, creationflags=no_window_flags(),
    )


def spawn_hidden():
    """Start the service with no console window (used by the Windows logon task)."""
    bash = _find_bash()
    if not bash:
        write_log("ERROR", "AUTOSTART", "Git Bash was not found; cannot start the connector")
        return False
    script = _msys_path(ROOT / "start.sh")
    if " " in script:
        script = f"'{script}'"
    LOGS.mkdir(exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        with (LOGS / "autostart.log").open("ab") as out:
            out.write(f"[{now()}] launch: {bash} -c exec {script}\n".encode("utf-8"))
            out.flush()
            subprocess.Popen(
                [bash, "-c", f"exec {script}"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                close_fds=True,
            )
    except Exception as e:
        write_log("ERROR", "AUTOSTART", f"hidden launch failed: {type(e).__name__}: {e}")
        return False
    write_log("INFO", "AUTOSTART", "started silently (no console window)")
    return True


def _autostart_windows_install():
    bash = _find_bash()
    if not bash:
        write_log("ERROR", "AUTOSTART", "Git Bash was not found; cannot register the logon task")
        return False
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pythonw = Path(sys.executable)
    # pythonw is a GUI-subsystem executable, so Task Scheduler never opens a
    # console; the launcher then gives bash CREATE_NO_WINDOW as well.
    command = str(pythonw)
    engine_py = ROOT / "engine.py"
    argument = f'"{engine_py}" spawn'
    account = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    if domain:
        account = f"{domain}\\{account}"
    task = _ps_quote(AUTOSTART_WINDOWS_TASK)
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$action = New-ScheduledTaskAction -Execute {_ps_quote(command)} "
        f"-Argument {_ps_quote(argument)} -WorkingDirectory {_ps_quote(str(ROOT))}\n"
        f"$trigger = New-ScheduledTaskTrigger -AtLogOn -User {_ps_quote(account)}\n"
        "$trigger.Delay = 'PT10S'\n"
        "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew "
        "-ExecutionTimeLimit ([TimeSpan]::Zero)\n"
        f"$principal = New-ScheduledTaskPrincipal -UserId {_ps_quote(account)} "
        "-LogonType Interactive -RunLevel Limited\n"
        f"Register-ScheduledTask -TaskName {task} -Action $action -Trigger $trigger "
        "-Settings $settings -Principal $principal "
        "-Description 'Start the Doomsday Connector MCP server at logon (silent)' -Force | Out-Null\n"
    )
    proc = _run_powershell(script)
    if proc.returncode != 0:
        detail = ((proc.stderr or "") + (proc.stdout or "")).strip().replace("\n", " ")
        write_log("ERROR", "AUTOSTART", f"Scheduled task registration failed: {detail[:300]}")
        return False
    write_log(
        "INFO", "AUTOSTART",
        f"registered silent logon task '{AUTOSTART_WINDOWS_TASK}' account={account} via {command}",
    )
    return True


def _autostart_windows_remove():
    task = _ps_quote(AUTOSTART_WINDOWS_TASK)
    script = (
        f"$t = Get-ScheduledTask -TaskName {task} -ErrorAction SilentlyContinue\n"
        "if (-not $t) { 'NOT_INSTALLED'; exit 0 }\n"
        f"Unregister-ScheduledTask -TaskName {task} -Confirm:$false\n"
        "'REMOVED'\n"
    )
    proc = _run_powershell(script, timeout=60)
    if proc.returncode != 0:
        write_log("ERROR", "AUTOSTART", f"Scheduled task removal failed: {(proc.stderr or proc.stdout)[:300]}")
        return False
    write_log("INFO", "AUTOSTART", f"removed logon task '{AUTOSTART_WINDOWS_TASK}'")
    return True


def _autostart_windows_status():
    task = _ps_quote(AUTOSTART_WINDOWS_TASK)
    script = (
        f"$t = Get-ScheduledTask -TaskName {task} -ErrorAction SilentlyContinue\n"
        "if (-not $t) { 'NOT_INSTALLED'; exit 0 }\n"
        f"$i = Get-ScheduledTaskInfo -TaskName {task}\n"
        "'STATE=' + $t.State\n"
        "'LAST_RUN=' + $i.LastRunTime\n"
        "'LAST_RESULT=' + $i.LastTaskResult\n"
    )
    proc = _run_powershell(script, timeout=60)
    raw = (proc.stdout or "").strip()
    info = {
        "platform": "windows",
        "task": AUTOSTART_WINDOWS_TASK,
        "installed": bool(raw) and "NOT_INSTALLED" not in raw,
    }
    for line in raw.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            info[key.strip().lower()] = value.strip()
    if proc.returncode != 0 and not raw:
        info["error"] = (proc.stderr or "").strip()[:200]
    return info


def _linux_unit_path():
    return Path.home() / ".config" / "systemd" / "user" / "doomsday-connector.service"


def _autostart_linux_install():
    unit = _linux_unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Unit]\n"
        "Description=Doomsday Connector MCP server\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        f"WorkingDirectory={ROOT}\n"
        f"ExecStart={shlex.quote(str(ROOT / 'start.sh'))}\n"
        f"ExecStop={shlex.quote(str(ROOT / 'stop.sh'))}\n"
        "TimeoutStartSec=120\n\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )
    for script in (ROOT / "start.sh", ROOT / "stop.sh"):
        try:
            os.chmod(script, 0o755)
        except OSError:
            pass
    for args in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "doomsday-connector.service"],
    ):
        proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().replace("\n", " ")
            write_log("ERROR", "AUTOSTART", f"{' '.join(args)} failed: {detail[:200]}")
            return False
    write_log("INFO", "AUTOSTART", f"systemd user unit enabled: {unit}")
    return True


def _autostart_linux_remove():
    unit = _linux_unit_path()
    subprocess.run(["systemctl", "--user", "disable", "doomsday-connector.service"],
                   capture_output=True, timeout=60)
    unit.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=60)
    write_log("INFO", "AUTOSTART", "systemd user unit removed")
    return True


def _termux_boot_script_path():
    return Path.home() / ".termux" / "boot" / "doomsday-connector.sh"


def _autostart_termux_install():
    script = _termux_boot_script_path()
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "#!/data/data/com.termux/files/usr/bin/sh\n"
        "termux-wake-lock 2>/dev/null || true\n"
        f"cd {shlex.quote(str(ROOT))} || exit 1\n"
        "./start.sh >/dev/null 2>&1\n",
        encoding="utf-8",
    )
    try:
        os.chmod(script, 0o755)
    except OSError:
        pass
    write_log("INFO", "AUTOSTART",
              f"termux boot script written: {script} (requires the Termux:Boot app)")
    return True


def _autostart_termux_remove():
    _termux_boot_script_path().unlink(missing_ok=True)
    write_log("INFO", "AUTOSTART", "termux boot script removed")
    return True


def _darwin_plist_path():
    return Path.home() / "Library" / "LaunchAgents" / "dev.doomsday.connector.plist"


def _xml_escape(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _autostart_darwin_install():
    plist = _darwin_plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    plist.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "  <key>Label</key><string>dev.doomsday.connector</string>\n"
        "  <key>ProgramArguments</key>\n"
        f"  <array><string>{_xml_escape(ROOT / 'start.sh')}</string></array>\n"
        f"  <key>WorkingDirectory</key><string>{_xml_escape(ROOT)}</string>\n"
        "  <key>RunAtLoad</key><true/>\n"
        f"  <key>StandardOutPath</key><string>{_xml_escape(LOGS / 'autostart.log')}</string>\n"
        f"  <key>StandardErrorPath</key><string>{_xml_escape(LOGS / 'autostart.err.log')}</string>\n"
        "</dict>\n"
        "</plist>\n",
        encoding="utf-8",
    )
    try:
        os.chmod(ROOT / "start.sh", 0o755)
    except OSError:
        pass
    proc = subprocess.run(["launchctl", "load", "-w", str(plist)],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")
        write_log("ERROR", "AUTOSTART", f"launchctl load failed: {detail[:200]}")
        return False
    write_log("INFO", "AUTOSTART", f"launchd agent loaded: {plist}")
    return True


def _autostart_darwin_remove():
    plist = _darwin_plist_path()
    subprocess.run(["launchctl", "unload", "-w", str(plist)], capture_output=True, timeout=60)
    plist.unlink(missing_ok=True)
    write_log("INFO", "AUTOSTART", "launchd agent removed")
    return True


def autostart_install():
    kind = platform_kind()
    if kind == "windows":
        return _autostart_windows_install()
    if kind == "termux":
        return _autostart_termux_install()
    if kind == "linux":
        return _autostart_linux_install()
    if kind == "darwin":
        return _autostart_darwin_install()
    write_log("ERROR", "AUTOSTART", f"autostart is not implemented for platform '{kind}'")
    return False


def autostart_remove():
    kind = platform_kind()
    if kind == "windows":
        return _autostart_windows_remove()
    if kind == "termux":
        return _autostart_termux_remove()
    if kind == "linux":
        return _autostart_linux_remove()
    if kind == "darwin":
        return _autostart_darwin_remove()
    return False


def autostart_status():
    kind = platform_kind()
    if kind == "windows":
        return _autostart_windows_status()
    if kind == "termux":
        path = _termux_boot_script_path()
    elif kind == "linux":
        path = _linux_unit_path()
    elif kind == "darwin":
        path = _darwin_plist_path()
    else:
        return {"platform": kind, "installed": False, "error": "unsupported platform"}
    return {"platform": kind, "installed": path.is_file(), "path": str(path)}


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
    if mode == "spawn":
        return 0 if spawn_hidden() else 1
    if mode == "autostart-install":
        ok = autostart_install()
        print("Autostart installed." if ok else "Autostart install failed (see logs/connector.log).")
        return 0 if ok else 1
    if mode == "autostart-remove":
        ok = autostart_remove()
        print("Autostart removed." if ok else "Autostart removal failed (see logs/connector.log).")
        return 0 if ok else 1
    if mode == "autostart-status":
        print(json.dumps(autostart_status(), indent=2, ensure_ascii=False))
        return 0
    print("usage: engine.py check|daemon|stop|init-config|env|status|log|spawn"
          "|autostart-install|autostart-remove|autostart-status")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
