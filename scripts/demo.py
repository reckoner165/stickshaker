"""Play stickshaker sounds for files, without the daemon.

    uv run scripts/demo.py FILE [FILE ...] [--kind save|load|memory] [--wav OUT.wav]
"""

import argparse
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from stickshaker.synth import CRUSH_BITS, CRUSH_RATE, SR, render


def write_wav(path: Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--kind", default="memory", choices=["save", "load", "memory"])
    ap.add_argument("--rate", type=int, default=CRUSH_RATE, help="internal render rate (lower = more aliasing)")
    ap.add_argument("--bits", type=int, default=CRUSH_BITS, help="quantizer depth (lower = more crush)")
    ap.add_argument("--wav", type=Path, help="write the last sound to a WAV instead of playing")
    args = ap.parse_args()

    for f in args.files:
        data = f.read_bytes()
        samples = render(data, kind=args.kind, rate=args.rate, bits=args.bits)
        print(f"{f.name}: {len(data):,} bytes -> {samples.size / SR:.2f}s ({args.kind}, {args.rate} Hz, {args.bits}-bit)")
        if args.wav:
            write_wav(args.wav, samples)
        else:
            sd.play(samples, SR)
            sd.wait()


if __name__ == "__main__":
    main()
