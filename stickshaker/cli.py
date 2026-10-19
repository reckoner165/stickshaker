"""stickshaker command line: daemon, hook install/uninstall, and doctor."""

import argparse
import json
import logging
import signal
import sys
from pathlib import Path

from stickshaker import doctor, install
from stickshaker.codex import CodexMemoryWatcher
from stickshaker.player import Player
from stickshaker.server import SOCKET_PATH, AlreadyRunning, HookServer


def run(verbose: bool) -> int:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    player = Player()
    watcher = CodexMemoryWatcher(player.submit)
    try:
        server = HookServer(player, SOCKET_PATH, codex_watcher=watcher)
    except AlreadyRunning as e:
        logging.error("%s", e)
        return 1
    watcher.ensure_started()
    # SIGTERM (e.g. from launchd) unwinds like Ctrl-C so the socket gets removed.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    logging.info("listening on %s", SOCKET_PATH)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
        server.server_close()
        player.close()
    return 0


def install_hooks(target: install.Target, settings_path: Path, debug: bool) -> int:
    try:
        settings = install.load(settings_path)
    except json.JSONDecodeError as e:
        print(f"{settings_path} isn't valid JSON, not touching it: {e}")
        return 1
    install.add_hooks(settings, debug, target)
    backup = install.save(settings_path, settings)
    mode = "debug (failures show as hook errors)" if debug else "silent"
    print(f"installed {len(target.hooks)} {target.name} hooks in {settings_path}, {mode}")
    if backup:
        print(f"backup: {backup}")
    if target is install.CODEX:
        print("Codex runs new hooks only after you trust them: open the Codex CLI and run /hooks.")
    return 0


def uninstall_hooks(settings_path: Path) -> int:
    try:
        settings = install.load(settings_path)
    except json.JSONDecodeError as e:
        print(f"{settings_path} isn't valid JSON, not touching it: {e}")
        return 1
    removed = install.remove_hooks(settings)
    if removed:
        backup = install.save(settings_path, settings)
        print(f"removed {removed} hooks from {settings_path}, backup: {backup}")
    else:
        print("no stickshaker hooks installed")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="stickshaker")
    sub = ap.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run the daemon (headless)")
    run_p.add_argument("-v", "--verbose", action="store_true", help="also log ignored events")
    inst = sub.add_parser("install-hooks", help="add hooks to Claude Code (or Codex) settings")
    inst.add_argument("--debug", action="store_true", help="let socket failures show as hook errors")
    uninst = sub.add_parser("uninstall-hooks", help="remove stickshaker's hooks")
    for p in (inst, uninst):
        p.add_argument("--codex", action="store_true", help="target Codex (~/.codex/hooks.json)")
        p.add_argument("--settings", type=Path, help="settings file (default: the agent's own)")
    sub.add_parser("doctor", help="check daemon, socket round trip and hooks")
    args = ap.parse_args()
    if args.command == "run":
        sys.exit(run(args.verbose))
    if args.command in ("install-hooks", "uninstall-hooks"):
        target = install.CODEX if args.codex else install.CLAUDE
        path = args.settings or target.settings_path
        if args.command == "install-hooks":
            sys.exit(install_hooks(target, path, args.debug))
        sys.exit(uninstall_hooks(path))
    if args.command == "doctor":
        sys.exit(doctor.run())


if __name__ == "__main__":
    main()
