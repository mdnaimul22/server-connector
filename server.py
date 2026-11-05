import ctypes
import hmac
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "logs" / "connector.log"
IS_WIN = os.name == "nt"
GIT_BASH_CANDIDATES = (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "bash.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git" / "bin" / "bash.exe",
)


def kill_tree(pid, group=True):
    """Kill a process and its children (POSIX process group or Windows tree)."""
    if IS_WIN:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=15, creationflags=no_window_flags())
        except Exception:
            pass
        return
    try:
        if group:
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def pid_alive(pid):
    """Liveness probe that never signals the target process on Windows."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if IS_WIN:
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def no_window_flags():
    """Hide console windows; a console-less parent would otherwise flash one."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0


def detached_kwargs():
    if IS_WIN:
        # CREATE_NO_WINDOW (not DETACHED_PROCESS): a detached child gets a brand
        # new console, which Windows 11 hands to WindowsTerminal and flashes on
        # screen. CREATE_NO_WINDOW gives it a hidden console instead.
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | no_window_flags()}
    return {"start_new_session": True}


def find_bash():
    """Locate a POSIX shell so Linux-style commands behave the same everywhere."""
    if not IS_WIN:
        return ""
    # Prefer Git's wrapper over usr/bin/bash.exe: the raw binary cannot resolve
    # its own /usr/bin PATH when launched without an MSYS environment.
    for candidate in GIT_BASH_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("bash") or ""


def resolve_shell_prefix(cfg):
    configured = (os.environ.get("SHELL_BIN") or cfg.get("SHELL_BIN") or "").strip()
    if configured:
        if configured.lower() in ("cmd", "cmd.exe", "powershell", "powershell.exe", "none"):
            return []
        candidate = Path(configured)
        if candidate.is_file():
            return [str(candidate), "-c"]
        found = shutil.which(configured)
        if found:
            return [found, "-c"]
    bash = find_bash()
    if bash:
        return [bash, "-c"]
    return []


def write_log(level, message):
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} [{level:<5}] [SERVER    ] {message}\n")
    except Exception:
        pass


def load_config():
    cfg = {}
    p = Path(__file__).resolve().parent / "config.yml"
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


CFG = load_config()
SHELL_PREFIX = resolve_shell_prefix(CFG)
TOKEN = os.environ.get("MCP_TOKEN") or CFG.get("MCP_TOKEN") or ""
if not TOKEN or TOKEN == "REPLACE_WITH_A_LONG_RANDOM_SECRET":
    raise RuntimeError("Valid MCP_TOKEN is required in config.yml or environment")
PORT = int(os.environ.get("PORT") or CFG.get("PORT") or "8765")
HOST = os.environ.get("HOST") or CFG.get("HOST") or "127.0.0.1"
LIMIT = 20000
MAX_REQUEST_BYTES = 1_048_576
MAX_TIMEOUT = 600
JOB_ID_RE = re.compile(r"^[0-9]{6}-[0-9a-f]{4}$")
VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
JOBS = Path.home() / ".mcp_jobs"
JOBS.mkdir(exist_ok=True)

S = {"type": "string"}
I = {"type": "integer"}


def obj(props, req):
    return {"type": "object", "properties": props, "required": req}


TOOLS = [
    {"name": "run_command", "description": "Run a shell command and wait (short tasks, default timeout 60s)",
     "inputSchema": obj({"command": S, "timeout": I, "cwd": S}, ["command"])},
    {"name": "start_job", "description": "Start a detached background command that survives server restarts. Returns job id",
     "inputSchema": obj({"command": S, "cwd": S}, ["command"])},
    {"name": "job_status", "description": "Job state and log tail. Without id lists all jobs",
     "inputSchema": obj({"id": S, "tail": I}, [])},
    {"name": "job_kill", "description": "Kill a background job's process group",
     "inputSchema": obj({"id": S}, ["id"])},
    {"name": "sys_info", "description": "CPU, memory, swap, load, temperature, top processes",
     "inputSchema": obj({}, [])},
    {"name": "read_file", "description": "Read a text file, optional 1-based line range",
     "inputSchema": obj({"path": S, "start": I, "end": I}, ["path"])},
    {"name": "write_file", "description": "Write a text file (overwrite)",
     "inputSchema": obj({"path": S, "content": S}, ["path", "content"])},
    {"name": "append_file", "description": "Append text to a file",
     "inputSchema": obj({"path": S, "content": S}, ["path", "content"])},
    {"name": "edit_file", "description": "Replace one exact unique occurrence of old with new in a file",
     "inputSchema": obj({"path": S, "old": S, "new": S}, ["path", "old", "new"])},
    {"name": "list_dir", "description": "List a directory with sizes",
     "inputSchema": obj({"path": S}, [])},
    {"name": "self_decide", "description": "Inspect health, processes and logs and return an evidence-based decision report",
     "inputSchema": obj({}, [])},
]


def clip(s, tail=False):
    if len(s) <= LIMIT:
        return s
    if tail:
        return f"[truncated {len(s) - LIMIT} chars]\n" + s[-LIMIT:]
    return s[:LIMIT] + f"\n[truncated {len(s) - LIMIT} chars]"


def build_cmd_env():
    env = os.environ.copy()
    extra = [str(ROOT / "bin"), str(Path.home() / "bin")]
    if IS_WIN:
        git_root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git"
        for sub in ("usr/bin", "mingw64/bin", "bin", "cmd"):
            candidate = git_root / sub
            if candidate.is_dir():
                extra.append(str(candidate))
    termux_bin = "/data/data/com.termux/files/usr/bin"
    if os.path.isdir(termux_bin):
        extra.append(termux_bin)
    sep = ";" if IS_WIN else ":"
    cur_path = env.get("PATH", "")
    parts = [p for p in extra if p and p not in cur_path.split(sep)]
    if parts:
        env["PATH"] = sep.join(parts + ([cur_path] if cur_path else []))
    return env


def sh(command, timeout=60, cwd="~"):
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    timeout = max(1, min(int(timeout), MAX_TIMEOUT))
    workdir = Path(os.path.expanduser(cwd)).resolve()
    if not workdir.is_dir():
        raise ValueError(f"cwd is not a directory: {workdir}")
    args = SHELL_PREFIX + [command] if SHELL_PREFIX else command
    p = subprocess.Popen(
        args, shell=not SHELL_PREFIX, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        cwd=str(workdir), env=build_cmd_env(), **detached_kwargs(),
    )
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p.pid)
        try:
            out, err = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return clip(f"timeout after {timeout}s (killed)\n--stdout--\n{out}\n--stderr--\n{err}", True), True
    return clip(f"exit={p.returncode}\n--stdout--\n{out}\n--stderr--\n{err}", True), p.returncode != 0


def run_command(command, timeout=60, cwd="~"):
    return sh(command, timeout, cwd)


JOB_RUNNER = '''import json, pathlib, subprocess, sys, time

d = pathlib.Path(__file__).resolve().parent
command = (d / "cmd").read_text(encoding="utf-8")
workdir = (d / "cwd").read_text(encoding="utf-8")
prefix_file = d / "shell.json"
prefix = json.loads(prefix_file.read_text(encoding="utf-8")) if prefix_file.is_file() else []
args = prefix + [command] if prefix else command
started = time.time()
rc = 127
with (d / "log").open("wb") as f:
    try:
        proc = subprocess.Popen(
            args, shell=not prefix, cwd=workdir, stdin=subprocess.DEVNULL,
            stdout=f, stderr=subprocess.STDOUT,
        )
        rc = proc.wait()
    except Exception as exc:
        f.write(("launcher error: %s: %s\\n" % (type(exc).__name__, exc)).encode("utf-8", "replace"))
(d / "exit").write_text(str(rc), encoding="utf-8")
(d / "secs").write_text(str(int(time.time() - started)), encoding="utf-8")
'''


def start_job(command, cwd="~"):
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    workdir = Path(os.path.expanduser(cwd)).resolve()
    if not workdir.is_dir():
        raise ValueError(f"cwd is not a directory: {workdir}")
    jid = time.strftime("%H%M%S-") + uuid.uuid4().hex[:4]
    d = JOBS / jid
    d.mkdir()
    (d / "cmd").write_text(command, encoding="utf-8")
    (d / "cwd").write_text(str(workdir), encoding="utf-8")
    (d / "runner.py").write_text(JOB_RUNNER, encoding="utf-8")
    (d / "shell.json").write_text(json.dumps(SHELL_PREFIX), encoding="utf-8")
    (d / "started").write_text(str(int(time.time())), encoding="utf-8")
    p = subprocess.Popen(
        [sys.executable, str(d / "runner.py")], cwd=str(workdir), env=build_cmd_env(),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **detached_kwargs(),
    )
    (d / "pid").write_text(str(p.pid), encoding="utf-8")
    return jid, False


def job_info(d, tail):
    started = int((d / "started").read_text())
    if (d / "exit").exists():
        state = f"done exit={(d / 'exit').read_text().strip()} secs={(d / 'secs').read_text().strip()}"
    elif (d / "pid").is_file() and pid_alive((d / "pid").read_text().strip()):
        state = f"running elapsed={int(time.time()) - started}s"
    else:
        state = "dead (killed)"
    log = (d / "log").read_text(encoding="utf-8", errors="replace") if (d / "log").exists() else ""
    return f"{d.name} [{state}] cmd={(d / 'cmd').read_text(encoding='utf-8', errors='replace')[:120]}", "\n".join(log.splitlines()[-tail:])


def job_status(id=None, tail=15):
    tail = max(0, min(int(tail), 200))
    if id:
        if not JOB_ID_RE.fullmatch(id):
            return "invalid job id", True
        d = JOBS / id
        if not d.is_dir():
            return "no such job", True
        head, body = job_info(d, tail)
        return clip(head + "\n" + body, True), False
    rows = [job_info(d, 0)[0] for d in sorted(JOBS.iterdir()) if d.is_dir()]
    return "\n".join(rows) or "no jobs", False


def job_kill(id):
    if not JOB_ID_RE.fullmatch(id):
        return "invalid job id", True
    d = JOBS / id
    if not d.is_dir() or not (d / "pid").is_file():
        return "no such job", True
    kill_tree(int((d / "pid").read_text().strip()))
    return f"killed {id}", False


def sys_info():
    if IS_WIN:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        mib = 1024 * 1024
        lines = [
            f"cpus={os.cpu_count()} platform={sys.platform} host={os.environ.get('COMPUTERNAME', '')}",
            f"memory: total={status.ullTotalPhys // mib}MB free={status.ullAvailPhys // mib}MB used={status.dwMemoryLoad}%",
            f"pagefile: total={status.ullTotalPageFile // mib}MB free={status.ullAvailPageFile // mib}MB",
            "top processes (working set):",
        ]
        try:
            p = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process | Sort-Object -Descending WorkingSet64 | Select-Object -First 8 "
                 "-Property Id,ProcessName,@{n='MB';e={[math]::Round($_.WorkingSet64/1MB)}} | "
                 "Format-Table -HideTableHeaders -AutoSize | Out-String -Width 110"],
                capture_output=True, text=True, timeout=25, creationflags=no_window_flags(),
            )
            lines.extend(ln.rstrip() for ln in p.stdout.splitlines() if ln.strip())
        except Exception as e:
            lines.append(f"ps error: {type(e).__name__}: {e}")
        return clip("\n".join(lines)), False
    cmd = (
        "nproc; uptime; free -m; "
        "for f in /sys/class/thermal/thermal_zone*/temp; do cat $f; done 2>/dev/null | head -3; "
        "ps -eo pid,rss,etime,args --sort=-rss 2>/dev/null | head -8 | cut -c1-110"
    )
    return sh(cmd, 20)


def read_file(path, start=None, end=None):
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(str(p))
    if p.stat().st_size > 5_000_000:
        raise ValueError("file exceeds 5 MB read limit")
    text = p.read_text(encoding="utf-8", errors="replace")
    if start or end:
        text = "\n".join(text.splitlines()[(start or 1) - 1:end])
    return clip(text), False


def write_file(path, content):
    p = Path(path).expanduser()
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return f"{len(content)} chars -> {p}", False


def append_file(path, content):
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(content)
    return f"appended {len(content)} chars -> {p}", False


def edit_file(path, old, new):
    p = Path(path).expanduser()
    text = p.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        return f"expected 1 match, found {n}", True
    updated = text.replace(old, new)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(updated)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return "ok", False


def list_dir(path="~"):
    rows = []
    for e in sorted(Path(path).expanduser().iterdir()):
        rows.append(f"{'d' if e.is_dir() else '-'} {e.lstat().st_size:>10} {e.name}")
    return "\n".join(rows) or "empty", False


def self_decide():
    root = Path(__file__).resolve().parent
    p = subprocess.run([os.sys.executable, "engine.py", "check"], cwd=str(root),
                       capture_output=True, text=True, timeout=20, creationflags=no_window_flags())
    output = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    return clip(output, True), p.returncode != 0


FUNCS = {f.__name__: f for f in (
    run_command, start_job, job_status, job_kill, sys_info,
    read_file, write_file, append_file, edit_file, list_dir, self_decide,
)}


def call_tool(params):
    name = params.get("name")
    fn = FUNCS.get(name)
    if fn is None:
        write_log("WARN", f"tool={name} error='unknown tool'")
        return {"content": [{"type": "text", "text": "unknown tool"}], "isError": True}
    try:
        text, err = fn(**(params.get("arguments") or {}))
        write_log("WARN" if err else "INFO", f"tool={name} status={'error' if err else 'ok'}")
    except Exception as e:
        text, err = f"{type(e).__name__}: {e}", True
        write_log("ERROR", f"tool={name} exception={type(e).__name__}: {e}")
    return {"content": [{"type": "text", "text": text}], "isError": err}


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        client = ((msg.get("params") or {}).get("clientInfo") or {}).get("name", "unknown")
        write_log("INFO", f"method=initialize client={client} protocol={asked}")
        result = {
            "protocolVersion": asked if asked in VERSIONS else VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "doomsday-connector", "version": "2.3.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        write_log("INFO", f"method=tools/list count={len(TOOLS)}")
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = call_tool(msg.get("params") or {})
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def authorized(self):
        path = self.path.split("?")[0].rstrip("/")
        if hmac.compare_digest(path, f"/mcp/{TOKEN}"):
            return True
        auth = self.headers.get("Authorization", "")
        return path == "/mcp" and hmac.compare_digest(auth, f"Bearer {TOKEN}")

    def reply(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.reply(400, b'{"error":"invalid content length"}')
        if size < 0 or size > MAX_REQUEST_BYTES:
            return self.reply(413, b'{"error":"request too large"}')
        raw = self.rfile.read(size)
        if not self.authorized():
            return self.reply(401, b'{"error":"unauthorized"}')
        try:
            data = json.loads(raw)
        except ValueError:
            return self.reply(400, b'{"error":"bad json"}')
        out = [r for r in map(handle, data) if r is not None] if isinstance(data, list) else handle(data)
        if not out:
            return self.reply(202)
        self.reply(200, json.dumps(out).encode())

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            body = json.dumps({"ok": True, "service": "doomsday-connector", "version": "2.3.0"}).encode()
            return self.reply(200, body)
        self.reply(405, b'{"error":"method not allowed"}')

    do_DELETE = do_GET


class MCPHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    try:
        srv = MCPHTTPServer((HOST, PORT), Handler)
        write_log("INFO", f"listening host={HOST} port={PORT}")
        srv.serve_forever()
    except Exception as e:
        write_log("ERROR", f"startup failed host={HOST} port={PORT}: {type(e).__name__}: {e}")
        raise
