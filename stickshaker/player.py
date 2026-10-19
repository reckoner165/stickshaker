"""Single-voice player: a worker thread renders and plays events in order.

The queue is small on purpose. A burst of events plays as a couple of sounds
rather than piling up into a long backlog; extras are dropped.
"""

import logging
import queue
import threading
import time
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from stickshaker import synth
from stickshaker.events import Event

log = logging.getLogger(__name__)

MAX_QUEUE = 2


def play_blocking(samples: np.ndarray) -> None:
    sd.play(samples, synth.SR)
    sd.wait()


class Player:
    def __init__(
        self,
        max_queue: int = MAX_QUEUE,
        render: Callable[..., np.ndarray] = synth.render,
        play: Callable[[np.ndarray], None] = play_blocking,
    ):
        self.enabled = True
        self._render = render
        self._play = play
        self._queue: queue.Queue[Event | None] = queue.Queue(max_queue)
        self._thread = threading.Thread(target=self._run, name="player", daemon=True)
        self._thread.start()

    def submit(self, event: Event) -> bool:
        """Queue an event without blocking. False if disabled or the queue is full."""
        if not self.enabled:
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            return False

    def close(self, timeout: float = 5.0) -> None:
        self._queue.put(None, timeout=timeout)
        self._thread.join(timeout)

    def _run(self) -> None:
        while (event := self._queue.get()) is not None:
            if not self.enabled:
                continue
            try:
                t0 = time.monotonic()
                samples = self._render(event.data, event.size, event.kind)
                t1 = time.monotonic()
                self._play(samples)
                t2 = time.monotonic()
                log.info(
                    "played %s: waited %.0f ms, render %.0f ms, play %.0f ms",
                    event.kind, (t0 - event.received) * 1000, (t1 - t0) * 1000, (t2 - t1) * 1000,
                )
            except Exception:
                log.exception("failed to play %s event", event.kind)
