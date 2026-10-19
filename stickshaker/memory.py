"""Detect agent memory writes by comparing a few known files to their last-seen state.

Scope is deliberately tiny: the session's own ~/.claude/projects/<project>/memory/
folder, ~/.claude/CLAUDE.md, and a CLAUDE.md/AGENTS.md only when a tool call
names that exact file. Nothing else on disk is ever listed or read.

A check runs only when a hook fires; between hooks nothing polls.
"""

import threading
import time
from collections.abc import Iterable
from pathlib import Path

CLAUDE_HOME = Path.home() / ".claude"
MEMORY_NAMES = {"CLAUDE.md", "AGENTS.md"}
RECENT_SECS = 5.0  # a first-seen file modified this recently counts as just written


def added_bytes(old: bytes, new: bytes) -> bytes:
    """Lines in `new` that weren't in `old`; the whole file if only lines were removed."""
    old_lines = set(old.splitlines(keepends=True))
    added = b"".join(line for line in new.splitlines(keepends=True) if line not in old_lines)
    return added or new


class MemoryTracker:
    def __init__(self, claude_home: Path = CLAUDE_HOME):
        self.projects = claude_home / "projects"
        self.user_memory = claude_home / "CLAUDE.md"
        self._seen: dict[Path, tuple[float, bytes]] = {}
        self._lock = threading.Lock()  # socket handlers run on separate threads

    def is_memory_file(self, path: Path) -> bool:
        if path.name in MEMORY_NAMES:
            return True
        # ~/.claude/projects/<project>/memory/<name>.md
        return path.suffix == ".md" and path.parent.name == "memory" and path.parent.parent.parent == self.projects

    def candidates(self, transcript_path: str, file_path: str = "") -> list[Path]:
        """The only files a check may look at for this hook call."""
        paths = [self.user_memory]
        transcript = Path(transcript_path) if transcript_path else None
        if transcript and transcript.parent.parent == self.projects:
            paths += sorted((transcript.parent / "memory").glob("*.md"))
        if file_path and self.is_memory_file(Path(file_path)):
            paths.append(Path(file_path))
        return list(dict.fromkeys(paths))

    def changes(self, paths: Iterable[Path]) -> bytes:
        """New or changed content across `paths` since the last check, updating snapshots."""
        now = time.time()
        chunks = []
        with self._lock:
            for p in paths:
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    self._seen.pop(p, None)
                    continue
                prev = self._seen.get(p)
                if prev and prev[0] == mtime:
                    continue
                try:
                    content = p.read_bytes()
                except OSError:
                    continue
                self._seen[p] = (mtime, content)
                if prev is None:
                    # First sighting is a silent baseline unless it was just written.
                    if now - mtime < RECENT_SECS:
                        chunks.append(content)
                elif content != prev[1]:
                    chunks.append(added_bytes(prev[1], content))
        return b"".join(chunks)
