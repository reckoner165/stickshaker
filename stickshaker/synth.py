"""ZX Spectrum tape-loading synth: bytes in, float32 waveform out.

Pure functions, no audio I/O. Timings are in Z80 T-states at 3.5 MHz,
straight from the Spectrum ROM's tape routines.
"""

import hashlib
from dataclasses import dataclass
from typing import Literal, NamedTuple

import numpy as np

Kind = Literal["save", "load", "memory"]

SR = 44100
CLK = 3_500_000

PILOT = 2168
SYNC1, SYNC2 = 667, 735
ZERO, ONE = 855, 1710

# Size sets a turbo between 1x (real Spectrum speed) and MAX_TURBO, on a log
# scale reaching the top at TURBO_FULL_KB. Much faster and the pulses get
# shorter than a sample and blur into noise.
MAX_TURBO = 1.4
TURBO_FULL_KB = 256
AMP = 0.1  # about -3.5 dB from the earlier 0.15

# Lo-fi character: internal render rate (aliasing), quantizer depth (crush),
# and pre-quantizer tape lowpass corner.
CRUSH_RATE = 22050  # 44100 / 2: whole-number hold (no jitter buzz); pulses stay several samples long
CRUSH_BITS = 4
TAPE_LP_HZ = 8000  # one-pole corner: rounds the square edges without washing out the tones

# Tape grit, levels relative to full scale. Steady hiss is just a low floor;
# the texture comes from noise that bursts, follows the signal, and clicks.
HISS = 0.008  # constant floor
DATA_NOISE = 0.5  # noise level follows each byte's value (cubed): spiky, data-driven
GRAIN_NOISE = 0.12  # noise gated on/off in random 2-20 ms grains
GRAIN_DENSITY = 0.3  # fraction of grains that are on
CRACKLE_PER_SEC = 25  # sparse clicks
DROPOUT = 0.5  # max depth of slow random gain dips

# Mix: the data section (bits at turbo speed, aliased) sounds like broadband
# noise, so it sits below the pilot tone. Ramp between them, and fade in gently.
DATA_LEVEL = 0.5  # -6 dB relative to pilot/sync
LEVEL_RAMP_SECS = 0.010
LINE_GAP_RAMP_SECS = 0.001
FADE_IN_SECS = 0.030
FADE_OUT_SECS = 0.005

# Output tone curve, matched to a spectrum of real Spectrum tape audio: little
# below 100 Hz, rising to flat just under 1 kHz. 2nd-order highpass magnitude.
HIGHPASS_HZ = 700



class Voice(NamedTuple):
    speed: float  # tape speed multiplier (pitch)
    pilot: float  # minimum fraction of the total duration given to the pilot tone
    header: bool  # play a Spectrum header block before the data


# Each kind sounds different from its first moment: pitch, pilot length, header.
VOICES: dict[str, Voice] = {
    "memory": Voice(speed=1.25, pilot=0.10, header=False),
    "save": Voice(speed=1.0, pilot=0.30, header=True),
    "load": Voice(speed=0.8, pilot=0.20, header=True),
}

# A hash of the content shifts tape speed by up to this much either way, so
# different content gets its own pitch. Kept below the gaps between voices.
CONTENT_PITCH = 0.10

# Each newline in the data adds a pause, in tape time (6 ms at real speed).
# Short-line code stutters, prose streams, one-line JSON runs continuously.
LINE_GAP_T = 21000


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


def size_turbo(size: int) -> float:
    """Bigger payloads play faster: 1x up to MAX_TURBO, log scale in size."""
    t = min(1.0, np.log2(1 + size / 1024) / np.log2(1 + TURBO_FULL_KB))
    return 1 + (MAX_TURBO - 1) * t


def content_pitch(data: bytes) -> float:
    """Deterministic speed factor in [1 - CONTENT_PITCH, 1 + CONTENT_PITCH] from the content."""
    h = int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "big") / 2**64
    return 1 + CONTENT_PITCH * (2 * h - 1)


def bit_pulses(block: bytes) -> np.ndarray:
    """Each bit is two equal half-pulses: 855 T for 0, 1710 T for 1, MSB first."""
    bits = np.unpackbits(np.frombuffer(block, dtype=np.uint8))
    return np.repeat(np.where(bits == 1, ONE, ZERO), 2).astype(np.float64)


def block_t(block: bytes) -> float:
    """Tape time of a block's data (bits plus newline pauses), in T-states."""
    return bit_pulses(block).sum() + LINE_GAP_T * block.count(b"\n")


def block_pulses(block: bytes, pilot_count: int) -> tuple[np.ndarray, np.ndarray]:
    """Half-pulse lengths for a block, plus a per-pulse level.

    Data pulses carry their byte value / 255. Pilot and sync carry -1, which
    marks them as not-data: no data grit, and full mix level. A newline pause
    is one long pulse carrying -2, which the mix silences.
    """
    raw = np.frombuffer(block, dtype=np.uint8)
    bits = bit_pulses(block)
    levels = np.repeat(raw / 255.0, 16)
    after_newlines = (np.flatnonzero(raw == 0x0A) + 1) * 16
    bits = np.insert(bits, after_newlines, LINE_GAP_T)
    levels = np.insert(levels, after_newlines, -2.0)
    lead = np.full(pilot_count, PILOT, dtype=np.float64)
    sync = np.array([SYNC1, SYNC2], dtype=np.float64)
    return np.concatenate([lead, sync, bits]), np.concatenate([np.full(pilot_count + 2, -1.0), levels])


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
    env = HISS + DATA_NOISE * np.clip(level, 0, None) ** 3 + GRAIN_NOISE * gate
    # Cubed gaussian (unit variance): mostly near-silent with sharp spikes.
    noise = rng.standard_normal(n) ** 3 / np.sqrt(15)
    x = wave + noise * env

    # One-pole tape lowpass (darkens the noise, turns edges into ramps to crush).
    a = 1 - np.exp(-2 * np.pi * TAPE_LP_HZ / rate)
    out = np.empty(n)
    y = 0.0
    for i, v in enumerate(x):
        y += a * (v - y)
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


def tone(x: np.ndarray) -> np.ndarray:
    """Zero-phase FFT EQ: apply the output tone curve to the whole signal."""
    n = x.size + SR // 10  # zero pad so the circular FFT doesn't wrap the tail
    f = np.fft.rfftfreq(n, 1 / SR)
    r = (f / HIGHPASS_HZ) ** 2
    gain = r / np.sqrt(1 + r**2)
    return np.fft.irfft(np.fft.rfft(x, n) * gain, n)[: x.size]


def smooth(x: np.ndarray, n: int) -> np.ndarray:
    """Moving average over `n` samples, same length as `x`."""
    n = max(1, n)
    return np.convolve(np.pad(x, n // 2, mode="edge"), np.ones(n) / n, mode="valid")[: x.size]


def crush(wave: np.ndarray, level: np.ndarray, rate: int, bits: int) -> np.ndarray:
    """Tape grit + lowpass, quantize to `bits`, mix data below pilot,
    zero-order-hold up to SR, tone curve, fade ends."""
    out = grit(wave, level, rate)
    levels = 2 ** (bits - 1)
    out = np.clip(np.round(out * levels), -levels, levels - 1) / levels
    mix = smooth(np.where(level == -1, 1.0, DATA_LEVEL), int(LEVEL_RAMP_SECS * rate))
    gate = smooth((level != -2).astype(float), int(LINE_GAP_RAMP_SECS * rate))
    out *= mix * gate
    # Sample-and-hold upsample, no interpolation filter: keeps the aliasing images.
    held = tone(out[(np.arange(int(out.size * SR / rate)) * rate // SR)]) * AMP
    for secs, end in ((FADE_IN_SECS, "in"), (FADE_OUT_SECS, "out")):
        n = min(int(secs * SR), held.size // 2)
        if n:
            ramp = np.linspace(0, 1, n)
            if end == "in":
                held[:n] *= ramp
            else:
                held[-n:] *= ramp[::-1]
    return held.astype(np.float32)


@dataclass(frozen=True)
class Plan:
    """Timing and pitch decisions for one sound; render() turns it into samples."""

    blocks: list[bytes]
    pilot_counts: list[int]
    scale: float  # seconds = T-states / CLK * scale
    gap_secs: float  # silence between blocks

    def seconds(self) -> float:
        tape = sum(c * PILOT + SYNC1 + SYNC2 + block_t(b) for b, c in zip(self.blocks, self.pilot_counts))
        return tape / CLK * self.scale + self.gap_secs * (len(self.blocks) - 1)


def plan(data: bytes, size: int | None = None, kind: Kind = "memory") -> Plan:
    """Decide blocks, pilot lengths and tape speed for a payload.

    Tape speed (pitch) = voice speed x content pitch x size turbo. Duration
    comes from `size`. The data plays at that speed; if it runs over, it is
    cut, and if it finishes early, the pilot tone fills the rest.
    """
    size = len(data) if size is None else size
    voice = VOICES[kind]
    scale = 1 / (voice.speed * content_pitch(data) * size_turbo(size))
    total = target_seconds(size)
    gap_secs = 0.12 if voice.header else 0.0
    budget = total * (1 - voice.pilot) - gap_secs

    blocks = ([header_block(kind, size)] if voice.header else []) + [data_block(data)]

    def data_secs(bs: list[bytes]) -> float:
        return sum(SYNC1 + SYNC2 + block_t(b) for b in bs) / CLK * scale

    # Too long at this speed: keep the longest prefix of the data that fits.
    if data_secs(blocks) > budget:
        lo, hi = 1, len(data)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if data_secs(blocks[:-1] + [data_block(data[:mid])]) <= budget:
                lo = mid
            else:
                hi = mid - 1
        blocks[-1] = data_block(data[:lo])

    # The pilot gets everything the data didn't use, at least voice.pilot.
    pilot_secs = total - gap_secs - data_secs(blocks)
    pilot_total = max(2, int(pilot_secs / (2 * PILOT / CLK * scale)) * 2)
    # The header gets a fifth of the pilot time; the data block gets the rest.
    header_count = (pilot_total // 10) * 2 if len(blocks) > 1 else 0
    counts = [header_count] * (len(blocks) - 1) + [pilot_total - header_count]
    return Plan(blocks, counts, scale, gap_secs)


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
    p = plan(data, size, kind)
    gap = np.zeros(int(p.gap_secs * rate))
    parts: list[np.ndarray] = []
    part_levels: list[np.ndarray] = []
    for i, (block, count) in enumerate(zip(p.blocks, p.pilot_counts)):
        wave, level = square(*block_pulses(block, count), p.scale, rate)
        parts.append(wave)
        part_levels.append(level)
        if i < len(p.blocks) - 1:
            parts.append(gap)
            part_levels.append(gap)
    return crush(np.concatenate(parts), np.concatenate(part_levels), rate, bits)
