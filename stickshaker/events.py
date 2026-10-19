"""Turn raw hook payloads into sound events, or None if the event stays silent.

This is the only place that knows which agent events make a sound and which
bytes get encoded for each.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any

from stickshaker.memory import MemoryTracker
from stickshaker.synth import Kind

TAIL_BYTES = 32 * 1024  # bytes of a transcript actually encoded for compaction
SUMMARY_SCAN_BYTES = 512 * 1024  # how far back to look for a compaction summary


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


def find_compact_summary(transcript_path: str) -> bytes | None:
    """Text of the most recent compaction summary entry in a Claude transcript.

    Unverified against a real compaction yet: looks for `isCompactSummary`.
    """
    tail = read_tail(transcript_path, SUMMARY_SCAN_BYTES)
    if tail is None:
        return None
    for line in reversed(tail[0].splitlines()):
        if b"isCompactSummary" not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not entry.get("isCompactSummary"):
            continue
        content = entry.get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        if content:
            return str(content).encode()
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

    if name == "SessionStart" and payload.get("source") == "compact":
        summary = find_compact_summary(transcript)
        if summary:
            return Event("load", summary, len(summary), "claude")
        tail = read_tail(transcript, TAIL_BYTES)
        return Event("load", tail[0], len(tail[0]), "claude") if tail and tail[0] else None

    return None
