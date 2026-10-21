import os
import shutil
import socket
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from stickshaker import cli, daemon, install, state

# Stands in for `stickshaker run`: listens on the socket, removes it on SIGTERM.
FAKE_DAEMON = """
import os, signal, socket, sys
path = sys.argv[1]
s = socket.socket(socket.AF_UNIX); s.bind(path); s.listen()
def bye(*_):
    os.unlink(path); sys.exit(0)
signal.signal(signal.SIGTERM, bye)
print("fake daemon up", flush=True)
while True:
    s.accept()[0].close()
"""


@pytest.fixture
def sock_path():
    # Short path: macOS limits Unix socket paths to ~104 bytes.
    d = tempfile.mkdtemp(prefix="ss")
    yield Path(d) / "s.sock"
    shutil.rmtree(d, ignore_errors=True)


def test_peer_pid_names_the_listener(sock_path):
    with socket.socket(socket.AF_UNIX) as s:
        s.bind(str(sock_path))
        s.listen()
        assert daemon.peer_pid(sock_path) == os.getpid()


def test_peer_pid_none_without_daemon(sock_path):
    assert daemon.peer_pid(sock_path) is None
    sock_path.touch()  # a stale file, nobody listening
    assert daemon.peer_pid(sock_path) is None


def test_start_status_stop(sock_path, tmp_path, capsys):
    log = tmp_path / "logs" / "stickshaker.log"
    cmd = [sys.executable, "-c", FAKE_DAEMON, str(sock_path)]
    assert daemon.start(socket_path=sock_path, log_path=log, command=cmd) == 0
    pid = daemon.peer_pid(sock_path)
    assert pid and pid != os.getpid()
    assert daemon.start(socket_path=sock_path, log_path=log, command=cmd) == 0  # no second daemon
    assert daemon.status(socket_path=sock_path, log_path=log) == 0
    assert daemon.stop(socket_path=sock_path) == 0
    assert not sock_path.exists()
    assert daemon.status(socket_path=sock_path, log_path=log) == 1
    out = capsys.readouterr().out
    assert f"started (pid {pid})" in out and "already running" in out and f"stopped (pid {pid})" in out
    assert "fake daemon up" in log.read_text()


def test_start_reports_early_exit(sock_path, tmp_path, capsys):
    log = tmp_path / "stickshaker.log"
    cmd = [sys.executable, "-c", "print('boom'); raise SystemExit(3)"]
    assert daemon.start(socket_path=sock_path, log_path=log, command=cmd) == 1
    out = capsys.readouterr().out
    assert "exited with code 3" in out and "boom" in out


def test_stop_without_daemon(sock_path, capsys):
    assert daemon.stop(socket_path=sock_path) == 0
    assert "not running" in capsys.readouterr().out


@pytest.fixture
def agents(tmp_path, monkeypatch):
    """Claude and Codex targets under tmp_path; neither agent folder exists yet."""
    claude = replace(install.CLAUDE, settings_path=tmp_path / ".claude" / "settings.json")
    codex = replace(install.CODEX, settings_path=tmp_path / ".codex" / "hooks.json")
    monkeypatch.setattr(install, "TARGETS", {"claude": claude, "codex": codex})
    monkeypatch.setattr(state, "set_enabled", lambda enabled: None)
    return claude, codex


def test_setup_installs_only_agents_found(agents):
    claude, codex = agents
    claude.settings_path.parent.mkdir()
    assert cli.setup([], debug=False) == 0
    assert install.installed_hooks(install.load(claude.settings_path))
    assert not codex.settings_path.exists()


def test_setup_with_no_agents_fails(agents, capsys):
    assert cli.setup([], debug=False) == 1
    assert "neither" in capsys.readouterr().out


def test_setup_explicit_agent(agents):
    claude, codex = agents
    assert cli.setup([codex], debug=False) == 0
    assert codex.settings_path.exists() and not claude.settings_path.exists()
