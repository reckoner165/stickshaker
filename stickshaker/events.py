"""Turn raw hook payloads into sound events, or None if the event stays silent.

This is the only place that knows which agent events make a sound and which
bytes get encoded for each.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from stickshaker.memory import MemoryTracker
from stickshaker.synth import Kind

TAIL_BYTES = 32 * 1024  # bytes of a transcript actually encoded for compaction


@dataclass(frozen=True)
class Event:
    kind: Kind
    data: bytes  # bytes encoded into the sound
    size: int  # logical size of the event; sets the duration
    source: str  # "claude" | "codex"
    received: float = field(default_factory=time.monotonic, compare=False)


def read_tail(path: str, n: int) -> tuple[bytes, int] | None:
    """Last `n` bytes of a file and its full size, or None if unreadable."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - n))
            return f.read(), size
    except OSError:
        return None


def from_hook(payload: dict[str, Any], tracker: MemoryTracker) -> Event | None:
    """Map a Claude Code hook payload to an Event.

    PostToolUse is only a trigger: whatever the tool was (Write, Edit, Bash...),
    the tracker decides from the memory files themselves whether anything changed.
    """
    name = payload.get("hook_event_name")
    transcript = payload.get("transcript_path", "")

    if name == "PostToolUse":
        tool_input = payload.get("tool_input") or {}
        paths = tracker.candidates(transcript, tool_input.get("file_path", ""))
        data = tracker.changes(paths)
        return Event("memory", data, len(data), "claude") if data else None

    if name == "PreCompact":
        tail = read_tail(transcript, TAIL_BYTES)
        return Event("save", tail[0], tail[1], "claude") if tail and tail[0] else None

    if name == "PostCompact":
        # Documented PostCompact input: `compact_summary` holds the new summary.
        summary = payload.get("compact_summary")
        if isinstance(summary, str) and summary:
            data = summary.encode()
            return Event("load", data, len(data), "claude")
        tail = read_tail(transcript, TAIL_BYTES)
        return Event("load", tail[0], len(tail[0]), "claude") if tail and tail[0] else None

    return None
