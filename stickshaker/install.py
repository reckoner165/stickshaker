"""Add/remove stickshaker's hooks in Claude Code's settings.json or Codex's hooks.json.

Merges rather than overwrites: only entries whose command talks to our socket
are touched, so other hooks and settings are left alone. Installing twice, or
switching between silent and debug mode, replaces our entries in place.
"""

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stickshaker.server import SOCKET_PATH

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
SOCKET_MARK = ".stickshaker.sock"  # how we recognise our own hook entries
HOOK_TIMEOUT = 5  # seconds; caps how long a wedged daemon could stall Claude

# (event, matcher) pairs; None means the event takes no matcher.
HOOKS: list[tuple[str, str | None]] = [
    ("PreCompact", None),
    ("PostCompact", None),
    # Any tool that can write files; the daemon checks the memory files themselves.
    ("PostToolUse", "Write|Edit|MultiEdit|NotebookEdit|Bash"),
]


@dataclass(frozen=True)
class Target:
    """An agent whose hook config we manage."""

    name: str
    settings_path: Path
    hooks: list[tuple[str, str | None]]
    tag: str | None  # source name added to the payload by the hook command


CLAUDE = Target("claude", SETTINGS_PATH, HOOKS, None)
# Codex: compaction only. Codex writes memories from a background job, not via
# tool calls, so the daemon watches ~/.codex/memories/ instead (codex.py).
# Hook format per https://learn.chatgpt.com/docs/hooks
CODEX = Target("codex", Path.home() / ".codex" / "hooks.json", [("PreCompact", None), ("PostCompact", None)], "codex")
TARGETS = {t.name: t for t in (CLAUDE, CODEX)}


def tag_command(source: str) -> str:
    """sed that inserts "stickshaker_source" as the first key of the JSON payload."""
    return f"""sed '1s/^{{/{{"stickshaker_source":"{source}",/'"""


def hook_command(debug: bool, target: Target = CLAUDE) -> str:
    """Silent mode swallows failures; debug mode lets them surface as hook errors.

    Debug exits non-zero (1), which Claude Code shows to the user as a
    non-blocking error. It never uses exit 2, which would feed into the agent.
    Codex documents no behavior for exit 1 on compaction hooks.
    """
    cmd = f"nc -U {SOCKET_PATH}"
    if target.tag:
        cmd = f"{tag_command(target.tag)} | {cmd}"
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


def add_hooks(settings: dict[str, Any], debug: bool, target: Target = CLAUDE) -> None:
    """Add our hook entries in place, replacing any previous ones of ours."""
    remove_hooks(settings)
    hooks = settings.setdefault("hooks", {})
    for event, matcher in target.hooks:
        group: dict[str, Any] = {}
        if matcher is not None:
            group["matcher"] = matcher
        group["hooks"] = [{"type": "command", "command": hook_command(debug, target), "timeout": HOOK_TIMEOUT}]
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
