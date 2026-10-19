import json

from stickshaker.events import Event, from_hook, is_memory_path

MEM = "/Users/x/.claude/projects/p/memory/user_prefs.md"


def post_tool(tool_name, **tool_input):
    return {"hook_event_name": "PostToolUse", "tool_name": tool_name, "tool_input": tool_input}


def test_memory_paths():
    assert is_memory_path(MEM)
    assert is_memory_path("/repo/CLAUDE.md")
    assert is_memory_path("/repo/AGENTS.md")
    assert not is_memory_path("/repo/src/memory.py")
    assert not is_memory_path("/repo/src/main.py")


def test_memory_write():
    assert from_hook(post_tool("Write", file_path=MEM, content="hello")) == Event(
        "memory", b"hello", 5, "claude"
    )


def test_memory_edit_uses_new_string():
    ev = from_hook(post_tool("Edit", file_path=MEM, old_string="a", new_string="bcd"))
    assert ev is not None and ev.data == b"bcd"


def test_non_memory_write_is_silent():
    assert from_hook(post_tool("Write", file_path="/repo/main.py", content="x")) is None


def test_empty_write_is_silent():
    assert from_hook(post_tool("Write", file_path=MEM, content="")) is None


def test_precompact_encodes_tail_but_sizes_whole_file(tmp_path, monkeypatch):
    monkeypatch.setattr("stickshaker.events.TAIL_BYTES", 10)
    t = tmp_path / "t.jsonl"
    t.write_bytes(b"x" * 100 + b"0123456789")
    ev = from_hook({"hook_event_name": "PreCompact", "transcript_path": str(t)})
    assert ev == Event("save", b"0123456789", 110, "claude")


def test_precompact_missing_transcript_is_silent():
    assert from_hook({"hook_event_name": "PreCompact", "transcript_path": "/nope"}) is None


def test_session_start_compact_uses_summary(tmp_path):
    t = tmp_path / "t.jsonl"
    summary = {"isCompactSummary": True, "message": {"content": [{"type": "text", "text": "SUMMARY"}]}}
    t.write_text(json.dumps({"type": "user"}) + "\n" + json.dumps(summary) + "\n")
    ev = from_hook({"hook_event_name": "SessionStart", "source": "compact", "transcript_path": str(t)})
    assert ev == Event("load", b"SUMMARY", 7, "claude")


def test_session_start_compact_falls_back_to_tail(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text('{"type": "user"}\n')
    ev = from_hook({"hook_event_name": "SessionStart", "source": "compact", "transcript_path": str(t)})
    assert ev is not None and ev.kind == "load" and ev.data == b'{"type": "user"}\n'


def test_session_start_other_sources_are_silent(tmp_path):
    assert from_hook({"hook_event_name": "SessionStart", "source": "startup"}) is None


def test_unknown_event_is_silent():
    assert from_hook({"hook_event_name": "Stop"}) is None
