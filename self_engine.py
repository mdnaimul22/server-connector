#!/usr/bin/env python3
"""Bounded decision and recovery engine for the Server Connector service."""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
STATE = ROOT / "self_engine_state.json"
DECISIONS = LOGS / "decisions.jsonl"
HEALTH = "http://127.0.0.1:" + os.environ.get("PORT", "8765") + "/health"
POLL_SECONDS = 60
MAX_LOG_BYTES = 250_000


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_json_atomic(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def record(event):
    LOGS.mkdir(exist_ok=True)
    event["time"] = now()
    with DECISIONS.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def health():
    try:
        with urllib.request.urlopen(HEALTH, timeout=3) as r:
            data = json.loads(r.read(4096).decode())
            return {"ok": r.status == 200 and data.get("ok") is True,
                    "status": r.status, "version": data.get("version")}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)[:160]}


def processes():
    try:
        p = subprocess.run(["sh", "-c", "ps -A -o pid,args | grep -E 'python server.py|sh ./supervisor.sh|ngrok http' | grep -v grep"],
                           capture_output=True, text=True, timeout=5)
        return p.stdout.splitlines()[:12]
    except Exception as e:
        return ["process inspection failed: " + str(e)[:120]]


def tail(path, limit=12000):
    try:
        if not path.exists():
            return ""
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size - limit))
            return f.read(limit).decode("utf-8", "replace")
    except Exception:
        return ""


def decide():
    h = health()
    proc = processes()
    server_log = tail(LOGS / "server.log")
    upgrade_log = tail(LOGS / "upgrade.log")
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text())
        except Exception:
            state = {}
    errors = {
        "address_in_use": server_log.count("Address already in use"),
        "tracebacks": server_log.count("Traceback (most recent call last):"),
        "upgrade_errors": upgrade_log.count("ERROR:"),
    }
    candidate = ROOT / "server.py.candidate"
    actions = []
    if not h["ok"]:
        actions.append({"action": "wait_and_recheck", "reason": "health endpoint unavailable; supervisor is responsible for restarting server"})
        decision = "SERVICE_UNHEALTHY_MONITOR_SUPERVISOR"
    elif "address_in_use" in state and errors["address_in_use"] > int(state.get("address_in_use", 0)):
        actions.append({"action": "report_supervisor_duplication", "reason": "new address-in-use errors observed; no process will be killed automatically"})
        decision = "INVESTIGATE_DUPLICATE_SERVER_STARTS"
    elif candidate.exists():
        actions.append({"action": "validate_candidate", "reason": "candidate detected; never install unvalidated code"})
        decision = "CANDIDATE_REQUIRES_VALIDATION"
    else:
        actions.append({"action": "no_change", "reason": "health is good and no staged upgrade exists"})
        decision = "HEALTHY_NO_CHANGE"
    report = {"decision": decision, "health": h, "errors": errors,
              "processes": proc, "actions": actions,
              "candidate_present": candidate.exists(),
              "policy": "bounded_actions; no arbitrary self-modification; backups and validation required"}
    state.update({"last_check": now(), "last_decision": decision,
                  "address_in_use": errors["address_in_use"],
                  "tracebacks": errors["tracebacks"], "upgrade_errors": errors["upgrade_errors"]})
    write_json_atomic(STATE, state)
    record(report)
    return report


def recover_server_if_needed(report):
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text())
        except Exception:
            state = {}
    if report.get("health", {}).get("ok"):
        state["unhealthy_checks"] = 0
        write_json_atomic(STATE, state)
        return
    failures = int(state.get("unhealthy_checks", 0)) + 1
    state["unhealthy_checks"] = failures
    write_json_atomic(STATE, state)
    if failures < 2:
        record({"decision": "RECOVERY_DEFERRED", "reason": "first failed local health check; recheck before restart"})
        return
    proc = subprocess.run(["sh", "-c", "ps -A -o pid,args | awk '$2 == \"python\" && $3 == \"server.py\" {print $1}'"],
                          capture_output=True, text=True, timeout=5)
    pids = [int(x) for x in proc.stdout.split() if x.isdigit() and int(x) != os.getpid()]
    if not pids:
        record({"decision": "RECOVERY_DEFERRED", "reason": "no server PID found; supervisor should relaunch it"})
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    record({"decision": "SERVER_RESTART_REQUESTED", "pids": pids, "reason": "two consecutive local health failures; supervisor will relaunch server"})
    state["unhealthy_checks"] = 0
    write_json_atomic(STATE, state)


def daemon():
    LOGS.mkdir(exist_ok=True)
    record({"decision": "ENGINE_STARTED", "actions": [{"action": "monitor", "interval_seconds": POLL_SECONDS}]})
    while True:
        try:
            report = decide()
            recover_server_if_needed(report)
        except Exception as e:
            record({"decision": "ENGINE_EXCEPTION", "error": type(e).__name__ + ": " + str(e)[:200]})
        time.sleep(POLL_SECONDS)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "check":
        print(json.dumps(decide(), indent=2, ensure_ascii=False))
        return 0
    if mode == "daemon":
        daemon()
        return 0
    print("usage: self_engine.py check|daemon")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
