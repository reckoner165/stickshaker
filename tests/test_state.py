import uuid

import pytest
from Foundation import NSUserDefaults

from stickshaker import state
from stickshaker.events import Event
from stickshaker.player import Player


@pytest.fixture
def suite():
    name = f"com.reckoner165.stickshaker.test-{uuid.uuid4().hex[:8]}"
    yield name
    NSUserDefaults.alloc().init().removePersistentDomainForName_(name)


def test_missing_value_counts_as_enabled(suite):
    assert state.get_enabled(suite) is True


def test_set_and_get_round_trip(suite):
    state.set_enabled(False, suite)
    assert state.get_enabled(suite) is False
    state.set_enabled(True, suite)
    assert state.get_enabled(suite) is True


def test_player_reports_played_events():
    seen = []
    done = __import__("threading").Event()
    player = Player(render=lambda *a: a, play=lambda r: None)
    player.on_played = lambda ev: (seen.append(ev.kind), done.set())
    player.submit(Event("memory", b"x", 1, "claude"))
    assert done.wait(3) and seen == ["memory"]
    player.close()


def test_play_restarts_portaudio_first(monkeypatch):
    # A stale device list after an output change makes the stream fail to open.
    import sounddevice as sd

    from stickshaker import player

    calls = []
    monkeypatch.setattr(sd, "_terminate", lambda: calls.append("terminate"))
    monkeypatch.setattr(sd, "_initialize", lambda: calls.append("initialize"))
    monkeypatch.setattr(sd, "play", lambda *a: calls.append("play"))
    monkeypatch.setattr(sd, "wait", lambda: calls.append("wait"))
    player.play_blocking(None)
    assert calls == ["terminate", "initialize", "play", "wait"]
