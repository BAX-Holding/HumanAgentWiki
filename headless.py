#!/usr/bin/env python3
"""Idempotent, unprivileged boot/cron keeper. Never kills a running process.

Run with this checkout's venv Python and explicit --pg-bin / --pg-data paths.
Configure authenticated WEB_HOST, WEB_AUTH, HF_HUB_OFFLINE in .env first.
"""
import argparse
import fcntl
import os
from pathlib import Path
import subprocess
import sys


def environment(root):
    env = dict(os.environ)
    for line in (root / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    if env.get("MCP_HOST", "127.0.0.1") != "127.0.0.1":
        raise ValueError("Headless MCP must be loopback-only")
    if env.get("WEB_HOST", "127.0.0.1") not in ("127.0.0.1", "::1"):
        auth = env.get("WEB_AUTH", "").split(":", 1)
        if len(auth) != 2 or not all(auth):
            raise ValueError("Authenticate every non-loopback web UI")
    if env.get("HF_HUB_OFFLINE") != "1":
        raise ValueError("Download the model first, then set HF_HUB_OFFLINE=1")
    # cron gives no locale; macOS PostgreSQL then aborts with "postmaster became
    # multithreaded during startup". A valid UTF-8 locale avoids it.
    if not env.get("LC_ALL"):
        env["LC_ALL"] = "en_US.UTF-8"
    return env


def alive(pid_file, command):
    try:
        pid = int(pid_file.read_text())
    except (OSError, ValueError):
        return False
    if pid < 1:
        return False
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                            capture_output=True, text=True, timeout=5)
    # macOS framework Python rewrites argv[0] to Python.app. Match the absolute
    # script + exact mode, not the venv symlink; PID alone is unsafe after reuse.
    suffix = " " + " ".join(command[1:])
    return result.returncode == 0 and result.stdout.strip().endswith(suffix)


def ensure(root, pg_bin, pg_data):
    os.umask(0o077)
    state = root / "state"
    state.mkdir(exist_ok=True, mode=0o700)
    with (state / "headless.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        env = environment(root)
        ctl = [str(pg_bin / "pg_ctl"), "-D", str(pg_data)]
        result = subprocess.run(ctl + ["status"], capture_output=True, timeout=10)
        if result.returncode == 3:
            subprocess.run(ctl + ["-l", str(state / "postgres.log"),
                                 "-o", "-h 127.0.0.1", "-w", "-t", "20", "start"],
                           check=True, timeout=25, env=env)
        elif result.returncode:
            raise RuntimeError("Cannot determine PostgreSQL state")
        for mode in ("serve", "web"):
            command = [str(root / ".venv/bin/python"), str(root / "cli.py"), mode]
            pid_file = state / f"{mode}.pid"
            if alive(pid_file, command):
                continue
            log_path = state / f"{mode}.log"
            if log_path.exists() and log_path.stat().st_size > 10 * 1024 * 1024:
                log_path.replace(log_path.with_suffix(".log.1"))
            with log_path.open("ab") as log:
                proc = subprocess.Popen(command, cwd=root, env=env,
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                        start_new_session=True)
            pid_file.write_text(str(proc.pid))
            print(f"Started {mode}, pid={proc.pid}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", required=True, type=Path)
    parser.add_argument("--pg-data", required=True, type=Path)
    args = parser.parse_args()
    ensure(Path(__file__).resolve().parent, args.pg_bin, args.pg_data)
