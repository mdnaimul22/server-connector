import hmac
import json
import os
import signal
import subprocess
import tempfile
import time
import uuid
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "logs" / "connector.log"


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
    termux_bin = "/data/data/com.termux/files/usr/bin"
    if os.path.isdir(termux_bin):
        extra.append(termux_bin)
    cur_path = env.get("PATH", "")
    parts = [p for p in extra if p and p not in cur_path.split(":")]
    if parts:
        env["PATH"] = ":".join(parts + ([cur_path] if cur_path else []))
    return env


def sh(command, timeout=60, cwd="~"):
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    timeout = max(1, min(int(timeout), MAX_TIMEOUT))
    workdir = Path(os.path.expanduser(cwd)).resolve()
    if not workdir.is_dir():
        raise ValueError(f"cwd is not a directory: {workdir}")
    p = subprocess.Popen(
        command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(workdir), env=build_cmd_env(), start_new_session=True,
    )
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, err = p.communicate()
        return clip(f"timeout after {timeout}s (killed)\n--stdout--\n{out}\n--stderr--\n{err}", True), True
    return clip(f"exit={p.returncode}\n--stdout--\n{out}\n--stderr--\n{err}", True), p.returncode != 0


def run_command(command, timeout=60, cwd="~"):
    return sh(command, timeout, cwd)


def start_job(command, cwd="~"):
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    workdir = Path(os.path.expanduser(cwd)).resolve()
    if not workdir.is_dir():
        raise ValueError(f"cwd is not a directory: {workdir}")
    jid = time.strftime("%H%M%S-") + uuid.uuid4().hex[:4]
    d = JOBS / jid
    d.mkdir()
    (d / "cmd").write_text(command)
    wrapper = (
        f"start=$(date +%s); ({command}) > {d}/log 2>&1; "
        f"echo $? > {d}/exit; echo $(( $(date +%s) - start )) > {d}/secs"
    )
    p = subprocess.Popen(
        ["sh", "-c", wrapper], cwd=str(workdir), env=build_cmd_env(), start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    (d / "pid").write_text(str(p.pid))
    (d / "started").write_text(str(int(time.time())))
    return jid, False


def job_info(d, tail):
    started = int((d / "started").read_text())
    if (d / "exit").exists():
        state = f"done exit={(d / 'exit').read_text().strip()} secs={(d / 'secs').read_text().strip()}"
    else:
        try:
            os.kill(int((d / "pid").read_text()), 0)
            state = f"running elapsed={int(time.time()) - started}s"
        except OSError:
            state = "dead (killed)"
    log = (d / "log").read_text(errors="replace") if (d / "log").exists() else ""
    return f"{d.name} [{state}] cmd={(d / 'cmd').read_text()[:120]}", "\n".join(log.splitlines()[-tail:])


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
    os.killpg(int((d / "pid").read_text()), signal.SIGKILL)
    return f"killed {id}", False


def sys_info():
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
    text = p.read_text(errors="replace")
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
        with os.fdopen(fd, "w") as f:
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
    with p.open("a") as f:
        f.write(content)
    return f"appended {len(content)} chars -> {p}", False


def edit_file(path, old, new):
    p = Path(path).expanduser()
    text = p.read_text()
    n = text.count(old)
    if n != 1:
        return f"expected 1 match, found {n}", True
    updated = text.replace(old, new)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w") as f:
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
                       capture_output=True, text=True, timeout=20)
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
            "serverInfo": {"name": "server-connector", "version": "2.3.0"},
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
            body = json.dumps({"ok": True, "service": "server-connector", "version": "2.3.0"}).encode()
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
