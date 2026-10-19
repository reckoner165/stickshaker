import json

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


def test_precompact_missing_transcript_is_silent(tracker):
    assert from_hook({"hook_event_name": "PreCompact", "transcript_path": "/nope"}, tracker) is None


def test_session_start_compact_uses_summary(tracker, tmp_path):
    t = tmp_path / "t.jsonl"
    summary = {"isCompactSummary": True, "message": {"content": [{"type": "text", "text": "SUMMARY"}]}}
    t.write_text(json.dumps({"type": "user"}) + "\n" + json.dumps(summary) + "\n")
    ev = from_hook({"hook_event_name": "SessionStart", "source": "compact", "transcript_path": str(t)}, tracker)
    assert ev == Event("load", b"SUMMARY", 7, "claude")


def test_session_start_compact_falls_back_to_tail(tracker, tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text('{"type": "user"}\n')
    ev = from_hook({"hook_event_name": "SessionStart", "source": "compact", "transcript_path": str(t)}, tracker)
    assert ev is not None and ev.kind == "load" and ev.data == b'{"type": "user"}\n'


def test_session_start_other_sources_are_silent(tracker, tmp_path):
    assert from_hook({"hook_event_name": "SessionStart", "source": "startup"}, tracker) is None


def test_unknown_event_is_silent(tracker):
    assert from_hook({"hook_event_name": "Stop"}, tracker) is None
