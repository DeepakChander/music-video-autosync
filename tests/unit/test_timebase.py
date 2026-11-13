from fractions import Fraction

import numpy as np
import pytest

from engine.timebase import (TICKS_PER_SECOND, parse_rate, seconds_to_ticks, snap_seconds_to_frame_ticks,
                             ticks_per_frame)


def test_ticks_per_frame_is_integer_for_common_rates():
    for r in ["24000/1001", "24", "25", "30000/1001", "30", "50", "60000/1001", "60"]:
        assert ticks_per_frame(parse_rate(r)).denominator == 1, r


def test_parse_rate_rejects_zero():
    with pytest.raises(ValueError):
        parse_rate("0/1")


def test_snap_to_nearest_frame():
    fps = Fraction(24000, 1001)
    tpf = int(ticks_per_frame(fps))
    ticks, err = snap_seconds_to_frame_ticks(72.75, fps)
    assert ticks % tpf == 0
    assert abs(err) <= 0.5 / float(fps) + 1e-12
    assert abs(ticks / TICKS_PER_SECOND - 72.75 - err) < 1e-9


def test_snap_accepts_numpy_floats():
    # Regression: the fine pass returns numpy.float32, which Fraction() rejects.
    fps = Fraction(25)
    for v in (np.float32(10.01), np.float64(10.01)):
        ticks, _ = snap_seconds_to_frame_ticks(v, fps)
        assert ticks == 10 * TICKS_PER_SECOND


def test_seconds_to_ticks_accepts_numpy_floats():
    assert seconds_to_ticks(np.float32(1.0)) == TICKS_PER_SECOND


def test_snap_zero_and_negative():
    fps = Fraction(25)
    assert snap_seconds_to_frame_ticks(0.0, fps)[0] == 0
    assert snap_seconds_to_frame_ticks(-0.05, fps)[0] == -int(ticks_per_frame(fps))
