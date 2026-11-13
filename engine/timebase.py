"""Premiere tick arithmetic. Ticks are integers; they are passed around as strings.

TICKS_PER_SECOND: Premiere Pro measures time in ticks, 254016000000 per second.
The UXP reference does not expose it as a constant; the value is VERIFIED from Adobe's official
sample (AdobeDocs/uxp-premiere-pro-samples, sample-panels/premiere-api/src/sequence.ts,
`const ticksPerSec = 254016000000;`) and the sample .sqpreset (30 fps = 8467200000 ticks/frame).
See docs/PREMIERE_API_NOTES.md item 8. Every common frame rate (23.976, 24, 25, 29.97, 30, 50,
59.94, 60) has an integer number of ticks per frame (tested in tests/unit/test_timebase.py).
Ticks travel as strings because TickTime.createWithTicks takes a string and doubles lose
precision after about 9.85 hours.
"""

from __future__ import annotations

import math
from fractions import Fraction

TICKS_PER_SECOND = 254016000000


def parse_rate(text: str) -> Fraction:
    """'24000/1001' -> Fraction(24000, 1001); '25' -> Fraction(25). Rejects 0 and negatives."""
    text = str(text).strip()
    if "/" in text:
        num, den = text.split("/", 1)
        value = Fraction(int(num), int(den))
    else:
        value = Fraction(text)
    if value <= 0:
        raise ValueError(f"frame rate must be positive, got {text!r}")
    return value


def ticks_per_frame(fps: Fraction) -> Fraction:
    return Fraction(TICKS_PER_SECOND) / fps


def seconds_to_ticks(seconds: float) -> int:
    return int(round(Fraction(float(seconds)).limit_denominator(10**9) * TICKS_PER_SECOND))


def ticks_to_seconds(ticks: int | str) -> float:
    return int(ticks) / TICKS_PER_SECOND


def snap_seconds_to_frame_ticks(seconds: float, fps: Fraction) -> tuple[int, float]:
    """Snap a time to the nearest sequence frame boundary.

    Returns (ticks, snap_error_s) where snap_error_s = snapped - exact.
    Rounds half away from zero on the frame index so the result is deterministic.
    """
    tpf = ticks_per_frame(fps)
    exact = Fraction(float(seconds)).limit_denominator(10**9)
    frames = exact * fps
    half = Fraction(1, 2)
    frame_index = math.floor(frames + half) if frames >= 0 else -math.floor(-frames + half)
    ticks_exact = frame_index * tpf
    ticks = int(round(ticks_exact))
    return ticks, float(Fraction(ticks, TICKS_PER_SECOND) - exact)


def frame_duration_s(fps: Fraction) -> float:
    return float(1 / fps)
