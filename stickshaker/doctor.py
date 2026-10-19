"""`stickshaker doctor`: check the daemon, the socket round trip, and the installed hooks."""

import json
import socket
import time
from pathlib import Path

from stickshaker import install
from stickshaker.server import SOCKET_PATH

# Shaped like a real memory write so it exercises the full path and plays a sound.
TEST_EVENT = {
    "hook_event_name": "PostToolUse",
    "tool_name": "Write",
    "tool_input": {"file_path": "/stickshaker/memory/doctor.md", "content": "stickshaker doctor"},
}


def round_trip(path: Path, payload: dict, timeout: float = 3.0) -> float:
    """Send one event like `nc -U` does; seconds until the daemon closes the connection."""
    start = time.perf_counter()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        s.sendall(json.dumps(payload).encode())
        s.recv(1)
    return time.perf_counter() - start


def run(socket_path: Path = SOCKET_PATH, settings_path: Path = install.SETTINGS_PATH) -> int:
    ok = True

    def report(passed: bool, msg: str) -> None:
        nonlocal ok
        ok &= passed
        print(f"{'ok ' if passed else 'ERR'}  {msg}")

    if not socket_path.exists():
        report(False, f"no socket at {socket_path}: is `stickshaker run` going?")
    else:
        try:
            ms = round_trip(socket_path, TEST_EVENT) * 1000
            report(ms < 100, f"daemon answered in {ms:.1f} ms (test sound queued)")
        except OSError as e:
            report(False, f"socket exists but daemon didn't answer: {e}")

    try:
        found = install.installed_hooks(install.load(settings_path))
    except json.JSONDecodeError as e:
        report(False, f"{settings_path} isn't valid JSON: {e}")
        return 1
    for event, _ in install.HOOKS:
        cmd = found.get(event)
        if cmd is None:
            report(False, f"{event} hook missing (run `stickshaker install-hooks`)")
        else:
            mode = "silent" if cmd == install.hook_command(debug=False) else "debug"
            report(True, f"{event} hook installed ({mode})")
    return 0 if ok else 1
