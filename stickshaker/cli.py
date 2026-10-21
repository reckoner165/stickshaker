"""stickshaker command line: setup, daemon control, hook install/uninstall, and doctor."""

import argparse
import json
import logging
import signal
import sys
import threading
from pathlib import Path

from stickshaker import daemon, doctor, install, state
from stickshaker.codex import CodexMemoryWatcher
from stickshaker.player import Player
from stickshaker.server import SOCKET_PATH, AlreadyRunning, HookServer


def run(verbose: bool, headless: bool) -> int:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    player = Player()
    player.enabled = state.get_enabled()
    watcher = CodexMemoryWatcher(player.submit)
    try:
        server = HookServer(player, SOCKET_PATH, codex_watcher=watcher)
    except AlreadyRunning as e:
        logging.error("%s", e)
        return 1
    watcher.ensure_started()
    logging.info("listening on %s (sounds %s)", SOCKET_PATH, "enabled" if player.enabled else "disabled")
    try:
        if headless:
            # SIGTERM (e.g. from launchd) unwinds like Ctrl-C so the socket gets removed.
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
            server.serve_forever()
        else:
            # Cocoa needs the main thread; the server runs beside it.
            from stickshaker import menubar

            threading.Thread(target=server.serve_forever, name="server", daemon=True).start()
            menubar.run(player)  # returns on Quit, Ctrl-C or SIGTERM
            server.shutdown()
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
    state.set_enabled(True)  # a fresh install always starts with sounds on
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


def setup(targets: list[install.Target], debug: bool) -> int:
    """Install hooks for the chosen agents, or for each agent whose folder exists."""
    if not targets:
        targets = [t for t in install.TARGETS.values() if t.settings_path.parent.is_dir()]
        if not targets:
            print("found neither ~/.claude nor ~/.codex: install Claude Code or Codex first")
            return 1
    for target in targets:
        if install_hooks(target, target.settings_path, debug):
            return 1
    print("next: `stickshaker start`, then start a new agent session")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="stickshaker")
    sub = ap.add_subparsers(dest="command", required=True)
    setup_p = sub.add_parser("setup", help="install hooks for Claude Code and Codex (each one found)")
    setup_p.add_argument("--claude", action="store_true", help="only Claude Code")
    setup_p.add_argument("--codex", action="store_true", help="only Codex")
    setup_p.add_argument("--debug", action="store_true", help="let socket failures show as hook errors")
    start_p = sub.add_parser("start", help="run the daemon in the background")
    start_p.add_argument("-v", "--verbose", action="store_true", help="also log ignored events")
    sub.add_parser("stop", help="stop the background daemon")
    sub.add_parser("status", help="show whether the daemon is running")
    run_p = sub.add_parser("run", help="run the daemon in this terminal, with a menu bar icon")
    run_p.add_argument("-v", "--verbose", action="store_true", help="also log ignored events")
    run_p.add_argument("--headless", action="store_true", help="no menu bar icon")
    inst = sub.add_parser("install-hooks", help="add hooks to Claude Code (or Codex) settings")
    inst.add_argument("--debug", action="store_true", help="let socket failures show as hook errors")
    uninst = sub.add_parser("uninstall-hooks", help="remove stickshaker's hooks")
    for p in (inst, uninst):
        p.add_argument("--codex", action="store_true", help="target Codex (~/.codex/hooks.json)")
        p.add_argument("--settings", type=Path, help="settings file (default: the agent's own)")
    sub.add_parser("doctor", help="check daemon, socket round trip and hooks")
    args = ap.parse_args()
    if args.command == "setup":
        chosen = [t for t, on in ((install.CLAUDE, args.claude), (install.CODEX, args.codex)) if on]
        sys.exit(setup(chosen, args.debug))
    if args.command == "start":
        sys.exit(daemon.start(args.verbose))
    if args.command == "stop":
        sys.exit(daemon.stop())
    if args.command == "status":
        sys.exit(daemon.status())
    if args.command == "run":
        sys.exit(run(args.verbose, args.headless))
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
