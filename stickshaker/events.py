"""Turn raw hook payloads into sound events, or None if the event stays silent.

This is the only place that knows which agent events make a sound and which
bytes get encoded for each.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stickshaker.synth import Kind

MEMORY_NAMES = {"CLAUDE.md", "AGENTS.md"}
TAIL_BYTES = 32 * 1024  # bytes of a transcript actually encoded for compaction
SUMMARY_SCAN_BYTES = 512 * 1024  # how far back to look for a compaction summary


@dataclass(frozen=True)
class Event:
    kind: Kind
    data: bytes  # bytes encoded into the sound
    size: int  # logical size of the event; sets the duration
    source: str  # "claude" | "codex"


def is_memory_path(path: str) -> bool:
    p = Path(path)
    return p.name in MEMORY_NAMES or "memory" in p.parts[:-1]


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


def written_text(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "Write":
        return tool_input.get("content", "")
    if tool_name == "Edit":
        return tool_input.get("new_string", "")
    if tool_name == "MultiEdit":
        return "".join(e.get("new_string", "") for e in tool_input.get("edits", []))
    return ""


def from_hook(payload: dict[str, Any]) -> Event | None:
    """Map a Claude Code hook payload to an Event."""
    name = payload.get("hook_event_name")
    transcript = payload.get("transcript_path", "")

    if name == "PostToolUse":
        tool_input = payload.get("tool_input") or {}
        if not is_memory_path(tool_input.get("file_path", "")):
            return None
        data = written_text(payload.get("tool_name", ""), tool_input).encode()
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
