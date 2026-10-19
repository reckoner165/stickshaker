"""Add/remove stickshaker's hooks in Claude Code's settings.json.

Merges rather than overwrites: only entries whose command talks to our socket
are touched, so other hooks and settings are left alone. Installing twice, or
switching between silent and debug mode, replaces our entries in place.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any

from stickshaker.server import SOCKET_PATH

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
SOCKET_MARK = ".stickshaker.sock"  # how we recognise our own hook entries
HOOK_TIMEOUT = 5  # seconds; caps how long a wedged daemon could stall Claude

# (event, matcher) pairs; None means the event takes no matcher.
HOOKS: list[tuple[str, str | None]] = [
    ("PreCompact", None),
    ("SessionStart", "compact"),
    # Any tool that can write files; the daemon checks the memory files themselves.
    ("PostToolUse", "Write|Edit|MultiEdit|NotebookEdit|Bash"),
]


def hook_command(debug: bool) -> str:
    """Silent mode swallows failures; debug mode lets them surface as hook errors.

    Debug exits non-zero (1), which Claude Code shows to the user as a
    non-blocking error. It never uses exit 2, which would feed into Claude.
    """
    cmd = f"nc -U {SOCKET_PATH}"
    return cmd if debug else f"{cmd} || true"


def is_ours(hook: dict[str, Any]) -> bool:
    return hook.get("type") == "command" and SOCKET_MARK in hook.get("command", "")


def remove_hooks(settings: dict[str, Any]) -> int:
    """Strip our hook entries in place, pruning anything left empty. Returns count removed."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in list(hooks):
        groups = []
        for group in hooks[event]:
            kept = [h for h in group.get("hooks", []) if not is_ours(h)]
            removed += len(group.get("hooks", [])) - len(kept)
            if kept:
                groups.append({**group, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return removed


def add_hooks(settings: dict[str, Any], debug: bool) -> None:
    """Add our hook entries in place, replacing any previous ones of ours."""
    remove_hooks(settings)
    hooks = settings.setdefault("hooks", {})
    for event, matcher in HOOKS:
        group: dict[str, Any] = {}
        if matcher is not None:
            group["matcher"] = matcher
        group["hooks"] = [{"type": "command", "command": hook_command(debug), "timeout": HOOK_TIMEOUT}]
        hooks.setdefault(event, []).append(group)


def installed_hooks(settings: dict[str, Any]) -> dict[str, str]:
    """Map of event -> our command, for events that have one of our entries."""
    found = {}
    for event, groups in (settings.get("hooks") or {}).items():
        for group in groups:
            for h in group.get("hooks", []):
                if is_ours(h):
                    found[event] = h["command"]
    return found


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save(path: Path, settings: dict[str, Any]) -> Path | None:
    """Back up the existing file, then write atomically. Returns the backup path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".stickshaker-bak")
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".stickshaker-tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    os.replace(tmp, path)
    return backup
