"""stickshaker command line: daemon, hook install/uninstall, and doctor."""

import argparse
import json
import logging
import signal
import sys
from pathlib import Path

from stickshaker import doctor, install
from stickshaker.player import Player
from stickshaker.server import SOCKET_PATH, AlreadyRunning, HookServer


def run(verbose: bool) -> int:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    player = Player()
    try:
        server = HookServer(player, SOCKET_PATH)
    except AlreadyRunning as e:
        logging.error("%s", e)
        return 1
    # SIGTERM (e.g. from launchd) unwinds like Ctrl-C so the socket gets removed.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    logging.info("listening on %s", SOCKET_PATH)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        player.close()
    return 0


def install_hooks(settings_path: Path, debug: bool) -> int:
    try:
        settings = install.load(settings_path)
    except json.JSONDecodeError as e:
        print(f"{settings_path} isn't valid JSON, not touching it: {e}")
        return 1
    install.add_hooks(settings, debug)
    backup = install.save(settings_path, settings)
    mode = "debug (failures show as hook errors)" if debug else "silent"
    print(f"installed {len(install.HOOKS)} hooks in {settings_path}, {mode}")
    if backup:
        print(f"backup: {backup}")
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
    inst = sub.add_parser("install-hooks", help="add hooks to Claude Code settings")
    inst.add_argument("--debug", action="store_true", help="let socket failures show as hook errors")
    uninst = sub.add_parser("uninstall-hooks", help="remove stickshaker's hooks")
    for p in (inst, uninst):
        p.add_argument("--settings", type=Path, default=install.SETTINGS_PATH)
    sub.add_parser("doctor", help="check daemon, socket round trip and hooks")
    args = ap.parse_args()
    if args.command == "run":
        sys.exit(run(args.verbose))
    if args.command == "install-hooks":
        sys.exit(install_hooks(args.settings, args.debug))
    if args.command == "uninstall-hooks":
        sys.exit(uninstall_hooks(args.settings))
    if args.command == "doctor":
        sys.exit(doctor.run())


if __name__ == "__main__":
    main()
