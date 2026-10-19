"""stickshaker command line: `stickshaker run` starts the headless daemon."""

import argparse
import logging
import signal
import sys

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


def main() -> None:
    ap = argparse.ArgumentParser(prog="stickshaker")
    sub = ap.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run the daemon (headless)")
    run_p.add_argument("-v", "--verbose", action="store_true", help="also log ignored events")
    args = ap.parse_args()
    if args.command == "run":
        sys.exit(run(args.verbose))


if __name__ == "__main__":
    main()
