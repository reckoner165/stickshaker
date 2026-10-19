import os
import time

import pytest

from stickshaker.events import from_hook
from stickshaker.memory import MemoryTracker, added_bytes


@pytest.fixture
def home(tmp_path):
    """A fake ~/.claude with one project, its transcript and memory folder."""
    project = tmp_path / "projects" / "-Users-x-repo"
    (project / "memory").mkdir(parents=True)
    (project / "session.jsonl").write_text("{}\n")
    return tmp_path


def transcript(home):
    return str(home / "projects" / "-Users-x-repo" / "session.jsonl")


def memfile(home, name="a.md"):
    return home / "projects" / "-Users-x-repo" / "memory" / name


def age(path, secs=60):
    t = time.time() - secs
    os.utime(path, (t, t))


def tool_call(home, tool="Bash", **tool_input):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "transcript_path": transcript(home),
        "tool_input": tool_input,
    }


def test_added_bytes():
    assert added_bytes(b"a\nb\n", b"a\nb\nc\n") == b"c\n"
    assert added_bytes(b"a\nb\n", b"a\n") == b"a\n"  # removal only: whole file


def test_candidates_stay_in_scope(home):
    m = memfile(home)
    m.write_text("x")
    t = MemoryTracker(home)
    paths = t.candidates(transcript(home), "/elsewhere/notes/memory/b.md")
    assert paths == [home / "CLAUDE.md", m]  # outside-~/.claude memory path ignored


def test_candidates_include_named_tool_target(home):
    t = MemoryTracker(home)
    assert t.candidates(transcript(home), "/repo/CLAUDE.md")[-1].as_posix() == "/repo/CLAUDE.md"


def test_transcript_outside_projects_adds_no_folder(home, tmp_path):
    t = MemoryTracker(home)
    assert t.candidates(str(tmp_path / "x.jsonl")) == [home / "CLAUDE.md"]


def test_old_file_first_seen_is_silent_baseline(home):
    m = memfile(home)
    m.write_text("old\n")
    age(m)
    t = MemoryTracker(home)
    assert from_hook(tool_call(home), t) is None


def test_bash_write_to_memory_plays(home):
    """The case that started this: memory edited via python/sed in a Bash call."""
    m = memfile(home)
    m.write_text("old\n")
    age(m)
    t = MemoryTracker(home)
    from_hook(tool_call(home), t)  # baseline
    m.write_text("old\nnew line\n")
    ev = from_hook(tool_call(home, command="cd memory && python3 - ..."), t)
    assert ev is not None and ev.kind == "memory" and ev.data == b"new line\n"


def test_just_written_new_file_plays(home):
    t = MemoryTracker(home)
    memfile(home, "fresh.md").write_text("hello\n")
    ev = from_hook(tool_call(home, "Write", file_path=str(memfile(home, "fresh.md"))), t)
    assert ev is not None and ev.data == b"hello\n"


def test_same_change_plays_once(home):
    t = MemoryTracker(home)
    memfile(home).write_text("hello\n")
    assert from_hook(tool_call(home, "Write"), t) is not None
    assert from_hook(tool_call(home), t) is None  # next Bash call: nothing new


def test_burst_across_files_is_one_event(home):
    t = MemoryTracker(home)
    memfile(home, "a.md").write_text("A\n")
    memfile(home, "MEMORY.md").write_text("index\n")
    ev = from_hook(tool_call(home), t)
    assert ev is not None and set(ev.data.splitlines()) == {b"A", b"index"}


def test_unrelated_bash_is_silent(home):
    m = memfile(home)
    m.write_text("x\n")
    age(m)
    t = MemoryTracker(home)
    from_hook(tool_call(home), t)
    assert from_hook(tool_call(home, command="ls"), t) is None
