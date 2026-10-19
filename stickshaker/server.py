"""Unix socket listener: one connection carries one hook JSON object, no reply.

The server closes the connection as soon as it has parsed one object, because
macOS `nc` holds the connection open until the other side closes it. Handlers
only parse, filter and queue; rendering happens on the player's thread.
"""

import json
import logging
import os
import socket
import socketserver
from pathlib import Path
from typing import Any

from stickshaker.events import from_hook
from stickshaker.player import Player

log = logging.getLogger(__name__)

SOCKET_PATH = Path.home() / ".stickshaker.sock"
MAX_PAYLOAD = 8 * 1024 * 1024
READ_TIMEOUT = 2.0


class AlreadyRunning(RuntimeError):
    pass


def read_one_json(conn: socket.socket) -> dict[str, Any] | None:
    """Read until one complete JSON object has arrived, EOF, or timeout."""
    conn.settimeout(READ_TIMEOUT)
    buf = b""
    while len(buf) < MAX_PAYLOAD:
        try:
            chunk = conn.recv(65536)
        except TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
        # Only try parsing when the data could be a complete object.
        if buf.rstrip().endswith(b"}"):
            try:
                obj = json.loads(buf)
                return obj if isinstance(obj, dict) else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
    try:
        obj = json.loads(buf)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def claim_socket(path: Path) -> None:
    """Exit if another daemon answers on `path`; remove the file if it's stale."""
    if not path.exists():
        return
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
        raise AlreadyRunning(f"another stickshaker is listening on {path}")
    except (ConnectionRefusedError, FileNotFoundError):
        path.unlink(missing_ok=True)
    finally:
        probe.close()


class _Handler(socketserver.BaseRequestHandler):
    server: "HookServer"

    def handle(self) -> None:
        payload = read_one_json(self.request)
        if payload is None:
            return
        name = payload.get("hook_event_name")
        event = from_hook(payload)
        if event is None:
            log.debug("ignored %s %s", name, payload.get("tool_name", ""))
            return
        queued = self.server.player.submit(event)
        log.info("%s %s (%d bytes) %s", name, event.kind, event.size, "queued" if queued else "dropped")


class HookServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, player: Player, path: Path = SOCKET_PATH):
        self.player = player
        self.path = Path(path)
        claim_socket(self.path)
        super().__init__(str(self.path), _Handler)
        os.chmod(self.path, 0o600)

    def server_close(self) -> None:
        super().server_close()
        self.path.unlink(missing_ok=True)
