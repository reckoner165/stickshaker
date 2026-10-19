"""Turn raw hook payloads into sound events, or None if the event stays silent.

This is the only place that knows which agent events make a sound and which
bytes get encoded for each.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stickshaker.memory import MemoryTracker
from stickshaker.synth import Kind

TAIL_BYTES = 32 * 1024  # bytes of a transcript actually encoded for compaction

# The daemon only reads files inside these folders, whatever a payload says.
ALLOWED_ROOTS: tuple[Path, ...] = (Path.home() / ".claude", Path.home() / ".codex")


@dataclass(frozen=True)
class Event:
    kind: Kind
    data: bytes  # bytes encoded into the sound
    size: int  # logical size of the event; sets the duration
    source: str  # "claude" | "codex"
    received: float = field(default_factory=time.monotonic, compare=False)


def is_allowed(path: str) -> bool:
    """True if `path`, after resolving links, is inside one of ALLOWED_ROOTS."""
    if not path:
        return False
    resolved = Path(path).resolve()
    return any(resolved.is_relative_to(root.resolve()) for root in ALLOWED_ROOTS)


def read_tail(path: str, n: int) -> tuple[bytes, int] | None:
    """Last `n` bytes of a file and its full size, or None if unreadable or not allowed."""
    if not is_allowed(path):
        return None
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - n))
            return f.read(), size
    except OSError:
        return None


def from_hook(payload: dict[str, Any], tracker: MemoryTracker) -> Event | None:
    """Map a hook payload from Claude Code or Codex to an Event.

    The Codex hook command tags its payload with "stickshaker_source": "codex".
    PostToolUse is only a trigger (Claude only): whatever the tool was, the
    tracker decides from the memory files themselves whether anything changed.
    """
    name = payload.get("hook_event_name")
    source = payload.get("stickshaker_source", "claude")
    transcript = payload.get("transcript_path", "")

    if name == "PostToolUse" and source == "claude":
        tool_input = payload.get("tool_input") or {}
        paths = tracker.candidates(transcript, tool_input.get("file_path", ""))
        data = tracker.changes(paths)
        return Event("memory", data, len(data), source) if data else None

    if name == "PreCompact":
        tail = read_tail(transcript, TAIL_BYTES)
        if tail and tail[0]:
            return Event("save", tail[0], tail[1], source)
        # Codex documents transcript_path as unstable; fall back to the payload.
        data = json.dumps(payload).encode()
        return Event("save", data, len(data), source)

    if name == "PostCompact":
        # Claude's PostCompact has `compact_summary`; Codex's does not.
        summary = payload.get("compact_summary")
        if isinstance(summary, str) and summary:
            data = summary.encode()
            return Event("load", data, len(data), source)
        tail = read_tail(transcript, TAIL_BYTES)
        data = tail[0] if tail and tail[0] else json.dumps(payload).encode()
        return Event("load", data, len(data), source)

    return None
