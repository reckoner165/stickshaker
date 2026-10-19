import json
import shutil
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from stickshaker.events import Event
from stickshaker.memory import MemoryTracker
from stickshaker.player import Player
from stickshaker.server import AlreadyRunning, HookServer


class FakeAudio:
    """Records what would have played; `gate` lets a test hold playback open."""

    def __init__(self):
        self.played: list[tuple[bytes, int, str]] = []
        self.gate = threading.Event()
        self.gate.set()
        self.done = threading.Semaphore(0)

    def render(self, data, size, kind):
        return (data, size, kind)

    def play(self, rendered):
        self.gate.wait(5)
        self.played.append(rendered)
        self.done.release()


@pytest.fixture
def sock_path():
    # Short path: macOS limits Unix socket paths to ~104 bytes, and pytest's
    # tmp_path is often longer than that.
    d = tempfile.mkdtemp(prefix="ss")
    yield Path(d) / "s.sock"
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def home(sock_path):
    """Fake ~/.claude next to the socket: one project with a memory folder."""
    project = sock_path.parent / "projects" / "p"
    (project / "memory").mkdir(parents=True)
    return sock_path.parent


def mem_write(home, name="a.md", content="hi"):
    """Write a memory file, and the PostToolUse payload Claude Code would send."""
    path = home / "projects" / "p" / "memory" / name
    path.write_text(content)
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "transcript_path": str(home / "projects" / "p" / "s.jsonl"),
        "tool_input": {"file_path": str(path), "content": content},
    }


@pytest.fixture
def daemon(sock_path, home):
    audio = FakeAudio()
    player = Player(render=audio.render, play=audio.play)
    server = HookServer(player, sock_path, MemoryTracker(home))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield audio, player, server
    server.shutdown()
    server.server_close()
    player.close()


def send(path: Path, payload: dict) -> bytes:
    """Send like `nc -U`: write, keep the socket open, read until the server closes."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        s.connect(str(path))
        s.sendall(json.dumps(payload).encode())
        return s.recv(1)  # b"" once the server closes its end


def test_memory_event_plays(daemon, sock_path, home):
    audio, _, _ = daemon
    assert send(sock_path, mem_write(home)) == b""  # server closed without nc half-closing
    assert audio.done.acquire(timeout=3)
    assert audio.played == [(b"hi", 2, "memory")]


def test_ignored_event_is_silent(daemon, sock_path, home):
    audio, _, _ = daemon
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    send(sock_path, payload)
    send(sock_path, mem_write(home))
    assert audio.done.acquire(timeout=3)
    assert audio.played == [(b"hi", 2, "memory")]


def test_socket_is_private(daemon, sock_path):
    assert sock_path.stat().st_mode & 0o777 == 0o600


def test_second_instance_refuses(daemon, sock_path):
    _, player, _ = daemon
    with pytest.raises(AlreadyRunning):
        HookServer(player, sock_path)


def test_stale_socket_is_replaced(sock_path):
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock_path))
    stale.close()  # file stays behind, nothing listening
    server = HookServer(Player(render=lambda *a: a, play=lambda r: None), sock_path)
    server.server_close()
    assert not sock_path.exists()


def test_burst_drops_extras():
    audio = FakeAudio()
    audio.gate.clear()  # hold the first sound "playing"
    player = Player(render=audio.render, play=audio.play)
    ev = lambda i: Event("memory", bytes([i]), 1, "claude")  # noqa: E731
    results = [player.submit(ev(0))]
    # Wait until the worker has taken event 0 off the queue and is "playing" it.
    for _ in range(100):
        if player._queue.empty():
            break
        threading.Event().wait(0.01)
    results += [player.submit(ev(i)) for i in range(1, 5)]
    assert results == [True, True, True, False, False]
    audio.gate.set()
    for _ in range(3):
        assert audio.done.acquire(timeout=3)
    assert [p[0] for p in audio.played] == [b"\x00", b"\x01", b"\x02"]
    player.close()


def test_disabled_player_drops():
    player = Player(render=lambda *a: a, play=lambda r: None)
    player.enabled = False
    assert player.submit(Event("memory", b"x", 1, "claude")) is False
    player.close()
