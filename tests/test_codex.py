"""Codex support, tested against payloads shaped as in https://learn.chatgpt.com/docs/hooks.

There is no Codex install to test against, so these pin the documented shapes.
"""

import json
import os
import subprocess
import threading
import time

import pytest

from stickshaker import install
from stickshaker.codex import CodexMemoryWatcher
from stickshaker.events import from_hook
from stickshaker.memory import MemoryTracker

# Common fields: session_id, transcript_path, cwd, hook_event_name, model.
# PreCompact/PostCompact add turn_id and trigger. No summary field.
CODEX_PRECOMPACT = {
    "session_id": "s1",
    "transcript_path": "/nonexistent/rollout.jsonl",
    "cwd": "/repo",
    "hook_event_name": "PreCompact",
    "model": "gpt-5",
    "turn_id": "t1",
    "trigger": "auto",
}
CODEX_POSTCOMPACT = {**CODEX_PRECOMPACT, "hook_event_name": "PostCompact"}


def tagged(payload: dict) -> dict:
    """Run the real tagging sed from the Codex hook command over a payload."""
    out = subprocess.run(
        install.tag_command("codex"), shell=True, input=json.dumps(payload), capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def tracker(tmp_path):
    return MemoryTracker(tmp_path)


def test_sed_tag_adds_source_and_keeps_payload():
    out = tagged(CODEX_PRECOMPACT)
    assert out.pop("stickshaker_source") == "codex"
    assert out == CODEX_PRECOMPACT


def test_codex_precompact_without_transcript_uses_payload(tracker):
    ev = from_hook(tagged(CODEX_PRECOMPACT), tracker)
    assert ev is not None and ev.kind == "save" and ev.source == "codex"
    assert b'"PreCompact"' in ev.data


def test_codex_precompact_with_transcript_uses_tail(tracker, tmp_path):
    t = tmp_path / "rollout.jsonl"
    t.write_bytes(b"x" * 10)
    ev = from_hook(tagged({**CODEX_PRECOMPACT, "transcript_path": str(t)}), tracker)
    assert ev is not None and ev.data == b"x" * 10 and ev.size == 10


def test_codex_postcompact_plays_load(tracker):
    ev = from_hook(tagged(CODEX_POSTCOMPACT), tracker)
    assert ev is not None and ev.kind == "load" and ev.source == "codex"


def test_codex_post_tool_use_is_ignored(tracker):
    payload = tagged({**CODEX_PRECOMPACT, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {}})
    assert from_hook(payload, tracker) is None


def test_codex_install_writes_compaction_hooks_only():
    s: dict = {}
    install.add_hooks(s, debug=False, target=install.CODEX)
    assert set(s["hooks"]) == {"PreCompact", "PostCompact"}
    cmd = s["hooks"]["PreCompact"][0]["hooks"][0]["command"]
    assert cmd.startswith("sed ") and "stickshaker_source" in cmd and cmd.endswith("|| true")


def test_codex_uninstall_leaves_other_hooks():
    other = {"type": "command", "command": "echo mine"}
    s = {"hooks": {"Stop": [{"hooks": [other]}]}}
    install.add_hooks(s, debug=False, target=install.CODEX)
    install.remove_hooks(s)
    assert s == {"hooks": {"Stop": [{"hooks": [other]}]}}


class Collector:
    def __init__(self):
        self.events = []
        self.got = threading.Semaphore(0)

    def submit(self, ev):
        self.events.append(ev)
        self.got.release()
        return True


def test_watcher_missing_folder_does_not_start(tmp_path):
    w = CodexMemoryWatcher(Collector().submit, tmp_path / "memories")
    assert w.ensure_started() is False and not w.running


def test_watcher_plays_one_sound_per_burst(tmp_path):
    folder = tmp_path / "memories"
    folder.mkdir()
    old = folder / "old.md"
    old.write_text("old memory\n")
    t = time.time() - 60
    os.utime(old, (t, t))
    c = Collector()
    w = CodexMemoryWatcher(c.submit, folder, debounce=0.3)
    assert w.ensure_started()
    try:
        time.sleep(0.5)  # let FSEvents settle before writing
        (folder / "a.md").write_text("first\n")
        (folder / "sub").mkdir()
        (folder / "sub" / "b.md").write_text("second\n")
        assert c.got.acquire(timeout=10)
        time.sleep(1.0)  # nothing else should arrive for the same burst
        assert len(c.events) == 1
        ev = c.events[0]
        assert ev.kind == "memory" and ev.source == "codex"
        assert set(ev.data.splitlines()) == {b"first", b"second"}  # old.md stayed a silent baseline
    finally:
        w.stop()


def test_watcher_skips_links_out_of_folder(tmp_path, tmp_path_factory):
    folder = tmp_path / "memories"
    folder.mkdir()
    outside = tmp_path_factory.mktemp("elsewhere") / "secret.md"
    outside.write_text("private\n")
    (folder / "link.md").symlink_to(outside)
    (folder / "real.md").write_text("ok\n")
    w = CodexMemoryWatcher(Collector().submit, folder)
    assert [p.name for p in w.files()] == ["real.md"]
