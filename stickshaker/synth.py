"""ZX Spectrum tape-loading synth: bytes in, float32 waveform out.

Pure functions, no audio I/O. Timings are in Z80 T-states at 3.5 MHz,
straight from the Spectrum ROM's tape routines.
"""

import zlib
from typing import Literal

import numpy as np

Kind = Literal["save", "load", "memory"]

SR = 44100
CLK = 3_500_000

PILOT = 2168
SYNC1, SYNC2 = 667, 735
ZERO, ONE = 855, 1710

MIN_SCALE = 0.25  # fastest: 4x turbo
MAX_SCALE = 1.0  # slowest: real Spectrum speed
AMP = 0.15

# Lo-fi character: internal render rate (aliasing), quantizer depth (crush),
# pre-quantizer tape lowpass coefficient, and hiss level (relative to full scale).
CRUSH_RATE = 8000
CRUSH_BITS = 4
TAPE_LP = 0.5

# Tape grit, levels relative to full scale. Steady hiss is just a low floor;
# the texture comes from noise that bursts, follows the signal, and clicks.
HISS = 0.008  # constant floor
DATA_NOISE = 0.5  # noise level follows each byte's value (cubed): spiky, data-driven
GRAIN_NOISE = 0.12  # noise gated on/off in random 2-20 ms grains
GRAIN_DENSITY = 0.3  # fraction of grains that are on
CRACKLE_PER_SEC = 25  # sparse clicks
DROPOUT = 0.5  # max depth of slow random gain dips

# Fraction of the total duration given to the pilot tone, per event kind.
PILOT_FRACTION: dict[str, float] = {"save": 0.30, "load": 0.20, "memory": 0.10}


def target_seconds(size: int) -> float:
    """Total duration grows with the log of the payload size."""
    kb = size / 1024
    return float(np.clip(0.4 + 0.6 * np.log2(1 + kb), 0.5, 4.0))


def checksum(block: bytes) -> int:
    x = 0
    for b in block:
        x ^= b
    return x


def header_block(kind: str, length: int) -> bytes:
    """17-byte Spectrum header (type 3 = CODE) wrapped as flag + body + checksum."""
    name = f"{kind:<10}"[:10].encode("ascii")
    body = bytes([3]) + name + min(length, 0xFFFF).to_bytes(2, "little") + bytes(4)
    block = bytes([0x00]) + body
    return block + bytes([checksum(block)])


def data_block(payload: bytes) -> bytes:
    block = bytes([0xFF]) + payload
    return block + bytes([checksum(block)])


def bit_pulses(block: bytes) -> np.ndarray:
    """Each bit is two equal half-pulses: 855 T for 0, 1710 T for 1, MSB first."""
    bits = np.unpackbits(np.frombuffer(block, dtype=np.uint8))
    return np.repeat(np.where(bits == 1, ONE, ZERO), 2).astype(np.float64)


def block_pulses(block: bytes, pilot_count: int) -> tuple[np.ndarray, np.ndarray]:
    """Half-pulse lengths for a block, plus a per-pulse level (byte value / 255).

    The level is 0 for pilot and sync, so only the data carries grit.
    """
    lead = np.full(pilot_count, PILOT, dtype=np.float64)
    sync = np.array([SYNC1, SYNC2], dtype=np.float64)
    byte_levels = np.frombuffer(block, dtype=np.uint8) / 255.0
    levels = np.concatenate([np.zeros(pilot_count + 2), np.repeat(byte_levels, 16)])
    return np.concatenate([lead, sync, bit_pulses(block)]), levels


def square(
    pulses_t: np.ndarray, levels: np.ndarray, scale: float, rate: int
) -> tuple[np.ndarray, np.ndarray]:
    """Half-pulse lengths (T-states) -> naive full-scale +/-1 square at `rate`,
    plus the per-pulse levels resampled to the same sample grid.

    No band-limiting on purpose: at low rates the edges alias. A slow sine on
    the time scale adds tape wow/flutter.
    """
    secs = pulses_t / CLK * scale
    t = np.cumsum(secs)
    secs = secs * (1 + 0.003 * np.sin(2 * np.pi * 0.5 * t))
    edges = np.cumsum(secs) * rate
    idx = np.searchsorted(edges, np.arange(int(edges[-1])), side="right")
    wave = np.where(idx % 2 == 0, 1.0, -1.0)
    return wave, levels[np.minimum(idx, levels.size - 1)]


def grit(wave: np.ndarray, level: np.ndarray, rate: int) -> np.ndarray:
    """Add uneven tape noise, run the tape lowpass, apply dropouts and crackle."""
    rng = np.random.default_rng()
    n = wave.size

    # Noise envelope: floor + data-driven level + random on/off grains.
    grain_lens = rng.integers(int(0.002 * rate) + 1, int(0.020 * rate) + 2, n // 16 + 1)
    grain_on = rng.random(grain_lens.size) < GRAIN_DENSITY
    gate = np.repeat(grain_on, grain_lens)[:n]
    env = HISS + DATA_NOISE * level**3 + GRAIN_NOISE * gate
    # Cubed gaussian (unit variance): mostly near-silent with sharp spikes.
    noise = rng.standard_normal(n) ** 3 / np.sqrt(15)
    x = wave + noise * env

    # One-pole tape lowpass (darkens the noise, turns edges into ramps to crush).
    out = np.empty(n)
    y = 0.0
    for i, v in enumerate(x):
        y += TAPE_LP * (v - y)
        out[i] = y

    # Dropouts: gain knots every ~30 ms, mostly near 1, occasionally deep.
    step = max(1, int(0.03 * rate))
    knots = np.arange(0, n + step, step)
    gains = 1 - DROPOUT * rng.random(knots.size) ** 4
    out *= np.interp(np.arange(n), knots, gains)

    # Crackle: sparse sharp clicks after the lowpass so they stay bright.
    clicks = np.zeros(n)
    hits = rng.random(n) < CRACKLE_PER_SEC / rate
    clicks[hits] = rng.uniform(-0.6, 0.6, hits.sum())
    out += np.convolve(clicks, [1.0, 0.5, 0.2], mode="same")
    return out


def crush(wave: np.ndarray, level: np.ndarray, rate: int, bits: int) -> np.ndarray:
    """Tape grit + lowpass, quantize to `bits`, zero-order-hold up to SR, fade ends."""
    out = grit(wave, level, rate)
    levels = 2 ** (bits - 1)
    out = np.clip(np.round(out * levels), -levels, levels - 1) / levels
    # Sample-and-hold upsample, no interpolation filter: keeps the aliasing images.
    held = out[(np.arange(int(out.size * SR / rate)) * rate // SR)] * AMP
    fade = min(int(0.005 * SR), held.size // 2)
    if fade:
        ramp = np.linspace(0, 1, fade)
        held[:fade] *= ramp
        held[-fade:] *= ramp[::-1]
    return held.astype(np.float32)


def render(
    data: bytes,
    size: int | None = None,
    kind: Kind = "memory",
    rate: int = CRUSH_RATE,
    bits: int = CRUSH_BITS,
) -> np.ndarray:
    """Render the tape sound for a payload.

    `size` is the logical size of the event (e.g. a whole transcript) and sets
    the duration; `data` is the bytes actually encoded. Defaults to len(data).
    """
    size = len(data) if size is None else size
    total = target_seconds(size)
    pilot_secs = total * PILOT_FRACTION[kind]
    gap_secs = 0.12 if kind == "save" else 0.0
    budget = total - pilot_secs - gap_secs

    payload = zlib.compress(data)
    blocks: list[bytes] = []
    if kind == "save":
        blocks.append(header_block(kind, size))
    blocks.append(data_block(payload))

    # Pick the turbo scale so the data bits fill the budget, within limits.
    real_secs = sum(bit_pulses(b).sum() for b in blocks) / CLK
    scale = float(np.clip(budget / real_secs, MIN_SCALE, MAX_SCALE))

    # Still too long at max turbo: truncate the data block to fit.
    if real_secs * scale > budget:
        fixed = sum(bit_pulses(b).sum() for b in blocks[:-1]) / CLK * scale
        bytes_secs = (ZERO + ONE) * 8 / CLK * scale  # average byte length
        keep = max(1, int((budget - fixed) / bytes_secs))
        blocks[-1] = data_block(payload[:keep])

    pilot_period = 2 * PILOT / CLK * scale
    pilot_total = max(2, int(pilot_secs / pilot_period) * 2)
    gap = np.zeros(int(gap_secs * rate))

    parts: list[np.ndarray] = []
    part_levels: list[np.ndarray] = []
    for i, block in enumerate(blocks):
        # Header gets a fifth of the pilot time; the data block gets the rest.
        header_count = (pilot_total // 10) * 2 if len(blocks) > 1 else 0
        count = header_count if i < len(blocks) - 1 else pilot_total - header_count
        wave, level = square(*block_pulses(block, count), scale, rate)
        parts.append(wave)
        part_levels.append(level)
        if i < len(blocks) - 1:
            parts.append(gap)
            part_levels.append(gap)
    return crush(np.concatenate(parts), np.concatenate(part_levels), rate, bits)
