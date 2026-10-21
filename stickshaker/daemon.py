"""Background daemon control: `stickshaker start`, `stop` and `status`.

The socket is the only state. If it accepts a connection, a daemon is running,
and macOS tells us its process ID (LOCAL_PEERPID), so there is no PID file.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from stickshaker.server import SOCKET_PATH

LOG_PATH = Path.home() / "Library" / "Logs" / "stickshaker.log"
START_TIMEOUT = 5.0
STOP_TIMEOUT = 10.0  # the daemon finishes a playing sound before it exits
POLL_SECS = 0.05

# <sys/un.h>: socket option level and name for the peer's process ID.
SOL_LOCAL = 0
LOCAL_PEERPID = 0x002


def peer_pid(path: Path = SOCKET_PATH) -> int | None:
    """Process ID of the daemon listening on `path`, or None if nothing answers."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        try:
            s.connect(str(path))
        except OSError:
            return None
        return s.getsockopt(SOL_LOCAL, LOCAL_PEERPID)


def run_command(verbose: bool) -> list[str]:
    return [sys.executable, "-m", "stickshaker", "run"] + (["-v"] if verbose else [])


def start(
    verbose: bool = False,
    socket_path: Path = SOCKET_PATH,
    log_path: Path = LOG_PATH,
    command: list[str] | None = None,
) -> int:
    if pid := peer_pid(socket_path):
        print(f"already running (pid {pid})")
        return 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as log:  # a new log on each start, so it stays small
        # A new session, so closing the terminal doesn't stop the daemon.
        proc = subprocess.Popen(
            command or run_command(verbose),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if pid := peer_pid(socket_path):
            print(f"started (pid {pid}), log: {log_path}")
            return 0
        if proc.poll() is not None:
            print(f"daemon exited with code {proc.returncode}, see {log_path}:")
            print(log_path.read_text().rstrip())
            return 1
        time.sleep(POLL_SECS)
    print(f"daemon didn't open {socket_path} within {START_TIMEOUT:.0f} s, see {log_path}")
    return 1


def stop(socket_path: Path = SOCKET_PATH) -> int:
    pid = peer_pid(socket_path)
    if not pid:
        print("not running")
        return 0
    os.kill(pid, signal.SIGTERM)  # same clean shutdown as Quit
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline:
        if not socket_path.exists():
            print(f"stopped (pid {pid})")
            return 0
        time.sleep(POLL_SECS)
    print(f"pid {pid} didn't stop within {STOP_TIMEOUT:.0f} s")
    return 1


def status(socket_path: Path = SOCKET_PATH, log_path: Path = LOG_PATH) -> int:
    pid = peer_pid(socket_path)
    print(f"running (pid {pid})" if pid else "not running")
    print(f"log: {log_path}")
    return 0 if pid else 1
