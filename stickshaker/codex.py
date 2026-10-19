"""Watch Codex's memory folder for background memory writes.

Codex generates memories in a background job after a chat goes idle, not via
tool calls, so no hook fires when it writes them. Per the Codex docs
(https://learn.chatgpt.com/docs/customization/memories.md) they live under
~/.codex/memories/. Memories are off by default; the folder may not exist.

Scope: that one folder only (FSEvents, recursive inside it). No polling.
"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from stickshaker.events import Event
from stickshaker.memory import MemoryTracker

log = logging.getLogger(__name__)

CODEX_MEMORIES = Path.home() / ".codex" / "memories"
DEBOUNCE_SECS = 1.0  # one background pass writes several files; play one sound


class CodexMemoryWatcher(FileSystemEventHandler):
    def __init__(
        self,
        submit: Callable[[Event], bool],
        folder: Path = CODEX_MEMORIES,
        debounce: float = DEBOUNCE_SECS,
    ):
        self.folder = folder
        self._submit = submit
        self._debounce = debounce
        self._tracker = MemoryTracker()
        self._observer: Observer | None = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._observer is not None

    def files(self) -> list[Path]:
        """Regular files inside the folder. Links that resolve outside it are skipped."""
        root = self.folder.resolve()
        return sorted(
            p
            for p in self.folder.rglob("*")
            if p.is_file() and not p.name.startswith(".") and p.resolve().is_relative_to(root)
        )

    def ensure_started(self) -> bool:
        """Start watching if the folder exists. Safe to call repeatedly."""
        with self._lock:
            if self._observer is not None:
                return True
            if not self.folder.is_dir():
                log.debug("no Codex memories folder at %s (memories off?)", self.folder)
                return False
            self._tracker.changes(self.files())  # silent baseline
            observer = Observer()
            observer.schedule(self, str(self.folder), recursive=True)
            observer.start()
            self._observer = observer
            log.info("watching Codex memories at %s", self.folder)
            return True

    def stop(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            if self._observer:
                self._observer.stop()
                self._observer.join(timeout=2)
                self._observer = None

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        data = self._tracker.changes(self.files())
        if not data:
            return
        queued = self._submit(Event("memory", data, len(data), "codex"))
        log.info("Codex memories -> memory, %d bytes, %s", len(data), "queued" if queued else "dropped")
