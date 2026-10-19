"""`stickshaker doctor`: check the daemon, the socket round trip, and the installed hooks."""

import json
import socket
import time
from pathlib import Path

from stickshaker import install
from stickshaker.codex import CODEX_MEMORIES
from stickshaker.server import SOCKET_PATH

# A PostCompact with a summary always maps to a sound (load), whatever the
# memory-file scope, so the test exercises the full path end to end.
TEST_EVENT = {
    "hook_event_name": "PostCompact",
    "trigger": "manual",
    "compact_summary": "stickshaker doctor test sound",
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


def run(
    socket_path: Path = SOCKET_PATH,
    settings_path: Path = install.SETTINGS_PATH,
    codex_path: Path = install.CODEX.settings_path,
    codex_memories: Path = CODEX_MEMORIES,
) -> int:
    ok = True

    def report(passed: bool, msg: str) -> None:
        nonlocal ok
        ok &= passed
        print(f"{'ok ' if passed else 'ERR'}  {msg}")

    def info(msg: str) -> None:
        print(f"--   {msg}")

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

    # Codex is optional: report what's there, never fail on it being absent.
    try:
        codex_found = install.installed_hooks(install.load(codex_path))
    except json.JSONDecodeError as e:
        report(False, f"{codex_path} isn't valid JSON: {e}")
        codex_found = {}
    if not codex_found:
        info("Codex hooks not installed (optional: `stickshaker install-hooks --codex`)")
    for event, _ in install.CODEX.hooks if codex_found else []:
        cmd = codex_found.get(event)
        if cmd is None:
            report(False, f"Codex {event} hook missing (run `stickshaker install-hooks --codex`)")
        else:
            mode = "silent" if cmd == install.hook_command(debug=False, target=install.CODEX) else "debug"
            report(True, f"Codex {event} hook installed ({mode}); trust it via /hooks in Codex")
    if codex_memories.is_dir():
        info(f"Codex memories folder found at {codex_memories}; the daemon watches it")
    else:
        info("no Codex memories folder (Codex memories are off by default)")
    return 0 if ok else 1
