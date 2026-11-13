from engine.layout import Span, assign_tracks, span_for
from engine.timebase import TICKS_PER_SECOND as T


def test_overlaps_go_to_separate_tracks_and_reuse_lowest():
    spans = [Span("a", "A", 0, 10 * T), Span("b", "B", 5 * T, 15 * T), Span("c", "C", 10 * T, 20 * T),
             Span("d", "D", 12 * T, 13 * T)]
    tracks = assign_tracks(spans)
    assert tracks == {"a": 1, "b": 2, "c": 1, "d": 3}


def test_end_equal_to_start_reuses_track():
    tracks = assign_tracks([Span("a", "A", 0, 5 * T), Span("b", "B", 5 * T, 6 * T)])
    assert tracks == {"a": 1, "b": 1}


def test_tie_break_by_name_is_deterministic():
    spans = [Span("z", "B", 0, T), Span("y", "A", 0, T)]
    assert assign_tracks(spans) == {"y": 1, "z": 2}
    assert assign_tracks(list(reversed(spans))) == {"y": 1, "z": 2}


def test_rate_two_clip_uses_song_time_length():
    sp = span_for("a", "A", 0, duration_s=10.0, source_in_s=0.0, rate=2.0)
    assert sp.end_ticks == 20 * T


def test_source_in_shortens_span():
    sp = span_for("a", "A", 0, duration_s=10.0, source_in_s=4.0, rate=1.0)
    assert sp.end_ticks == 6 * T


def test_low_clips_fill_gaps_without_pushing_placed_clips():
    from engine.layout import fill_gaps
    primary = [Span("a", "A", 0, 10 * T), Span("b", "B", 20 * T, 30 * T)]
    tracks = assign_tracks(primary)
    existing = {1: primary}
    low = fill_gaps([Span("l1", "L1", 11 * T, 19 * T), Span("l2", "L2", 5 * T, 8 * T)], existing)
    assert tracks == {"a": 1, "b": 1}
    assert low == {"l2": 2, "l1": 1}   # l1 fits the gap on V1; l2 overlaps a -> V2
