#!/data/data/com.termux/files/usr/bin/python
"""Validated, backup-first updater for the Termux MCP server."""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIVE = ROOT / "server.py"
CANDIDATE = ROOT / "server.py.candidate"
BACKUPS = ROOT / "backups"
LOG = ROOT / "logs" / "upgrade.log"
HEALTH = "http://127.0.0.1:" + os.environ.get("PORT", "8765") + "/health"


def log(message):
    stamp = datetime.now().isoformat(timespec="seconds")
    with LOG.open("a") as f:
        f.write(f"[{stamp}] {message}\n")


def compile_check(path):
    p = subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                       capture_output=True, text=True, timeout=20)
    if p.returncode:
        raise RuntimeError("compile failed: " + (p.stderr or p.stdout)[-3000:])
    smoke = "import runpy, os; os.environ.setdefault('MCP_TOKEN','validation-only'); runpy.run_path(" + repr(str(path)) + ", run_name='upgrade_validation')"
    p = subprocess.run([sys.executable, "-c", smoke], capture_output=True,
                       text=True, timeout=20)
    if p.returncode:
        raise RuntimeError("import smoke test failed: " + (p.stderr or p.stdout)[-3000:])


def health(timeout=2):
    try:
        with urllib.request.urlopen(HEALTH, timeout=timeout) as r:
            data = json.loads(r.read(4096).decode())
            return r.status == 200 and data.get("ok") is True
    except Exception:
        return False


def server_pids():
    p = subprocess.run(["sh", "-c", "ps -A -o pid,args | awk '$2 == \"python\" && $3 == \"server.py\" {print $1}'"],
                       capture_output=True, text=True, timeout=5)
    return [int(x) for x in p.stdout.split() if x.isdigit() and int(x) != os.getpid()]


def apply():
    if not CANDIDATE.is_file():
        raise RuntimeError(f"candidate missing: {CANDIDATE.name}")
    if CANDIDATE.stat().st_size > 2_000_000:
        raise RuntimeError("candidate exceeds 2 MB limit")
    compile_check(CANDIDATE)
    BACKUPS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUPS / ("upgrade-" + stamp)
    backup_dir.mkdir()
    backup = backup_dir / "server.py"
    shutil.copy2(LIVE, backup)
    log(f"backup created: {backup}")
    tmp = ROOT / ".server.py.upgrade-tmp"
    shutil.copyfile(CANDIDATE, tmp)
    with tmp.open("rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, LIVE)
    old_pids = server_pids()
    for pid in old_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + 30
    while time.time() < deadline:
        if health():
            CANDIDATE.unlink(missing_ok=True)
            log(f"upgrade succeeded; backup={backup}; pids_before={old_pids}")
            return f"UPGRADE_OK backup={backup_dir.name} health=200"
        time.sleep(1)
    log("new server health check failed; rolling back")
    shutil.copy2(backup, tmp)
    os.replace(tmp, LIVE)
    for pid in server_pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + 30
    while time.time() < deadline:
        if health():
            log(f"rollback succeeded; backup={backup}")
            raise RuntimeError(f"upgrade rolled back; previous server restored from {backup_dir.name}")
        time.sleep(1)
    log(f"CRITICAL: rollback server health not restored; backup={backup}")
    raise RuntimeError(f"rollback applied but health check failed; backup retained at {backup_dir}")


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "check"
    if action == "check":
        if not CANDIDATE.is_file():
            print("NO_CANDIDATE: write proposed code to server.py.candidate first")
            return 2
        compile_check(CANDIDATE)
        print(f"VALID: candidate compiles and imports; bytes={CANDIDATE.stat().st_size}")
        return 0
    if action == "apply":
        print(apply())
        return 0
    print("usage: self_upgrade.py check|apply")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"ERROR: {type(exc).__name__}: {exc}")
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
