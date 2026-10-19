import numpy as np
import pytest

from stickshaker import synth
from stickshaker.synth import CONTENT_PITCH, VOICES, block_pulses, content_pitch, plan, render, target_seconds

TEXT = b"def f(x):\n    return x + 1\n" * 4


def speed(p) -> float:
    """Effective tape speed of a plan relative to real Spectrum speed."""
    return 1 / p.scale


def test_plan_is_deterministic():
    assert plan(TEXT, kind="memory") == plan(TEXT, kind="memory")


def test_content_pitch_stays_in_range():
    for i in range(200):
        assert abs(content_pitch(bytes([i]) * 7) - 1) <= CONTENT_PITCH


def test_different_content_gets_different_pitch():
    a, b = plan(b"remember: user likes tape sounds"), plan(b"remember: user prefers SSH remotes")
    assert a.scale != b.scale


@pytest.mark.parametrize("data", [TEXT, b"x" * 50_000])
def test_kinds_have_distinct_pitch_order(data):
    s = {k: speed(plan(data, kind=k)) for k in VOICES}
    assert s["memory"] > s["save"] > s["load"]


def test_kinds_have_distinct_structure():
    assert len(plan(TEXT, kind="memory").blocks) == 1  # no header
    assert len(plan(TEXT, kind="save").blocks) == 2
    assert len(plan(TEXT, kind="load").blocks) == 2


@pytest.mark.parametrize("kind", list(VOICES))
@pytest.mark.parametrize("n", [20, 2_000, 200_000])
def test_duration_matches_target(kind, n):
    data = (b"some memory text\n" * (n // 17 + 1))[:n]
    p = plan(data, kind=kind)
    assert p.seconds() == pytest.approx(target_seconds(n), rel=0.05, abs=0.03)


def test_large_payload_is_truncated_to_fit():
    data = np.random.default_rng(0).bytes(100_000)
    p = plan(data, kind="save")
    assert len(p.blocks[-1]) < len(data)


def test_newlines_add_silent_pauses():
    _, levels = block_pulses(b"a\nb\nc", pilot_count=4)
    assert (levels == -2).sum() == 2


def test_render_length_matches_plan():
    p = plan(TEXT, kind="save")
    x = render(TEXT, kind="save")
    assert x.size / synth.SR == pytest.approx(p.seconds(), rel=0.02, abs=0.01)
