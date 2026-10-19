import pytest

from stickshaker.events import Event, from_hook
from stickshaker.memory import MemoryTracker


@pytest.fixture
def tracker(tmp_path):
    return MemoryTracker(tmp_path)


def test_precompact_encodes_tail_but_sizes_whole_file(tracker, tmp_path, monkeypatch):
    monkeypatch.setattr("stickshaker.events.TAIL_BYTES", 10)
    t = tmp_path / "t.jsonl"
    t.write_bytes(b"x" * 100 + b"0123456789")
    ev = from_hook({"hook_event_name": "PreCompact", "transcript_path": str(t)}, tracker)
    assert ev == Event("save", b"0123456789", 110, "claude")


def test_precompact_missing_transcript_falls_back_to_payload(tracker):
    ev = from_hook({"hook_event_name": "PreCompact", "transcript_path": "/nope"}, tracker)
    assert ev is not None and ev.kind == "save" and b"PreCompact" in ev.data


def test_post_compact_uses_compact_summary(tracker):
    payload = {"hook_event_name": "PostCompact", "trigger": "manual", "compact_summary": "SUMMARY"}
    assert from_hook(payload, tracker) == Event("load", b"SUMMARY", 7, "claude")


def test_post_compact_without_summary_falls_back_to_tail(tracker, tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text('{"type": "user"}\n')
    payload = {"hook_event_name": "PostCompact", "trigger": "auto", "transcript_path": str(t)}
    ev = from_hook(payload, tracker)
    assert ev is not None and ev.kind == "load" and ev.data == b'{"type": "user"}\n'


def test_session_start_is_silent(tracker):
    assert from_hook({"hook_event_name": "SessionStart", "source": "compact"}, tracker) is None


def test_unknown_event_is_silent(tracker):
    assert from_hook({"hook_event_name": "Stop"}, tracker) is None


def test_doctor_test_event_always_plays(tracker):
    from stickshaker.doctor import TEST_EVENT

    assert from_hook(TEST_EVENT, tracker) is not None
