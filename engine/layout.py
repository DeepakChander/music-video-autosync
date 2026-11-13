"""Deterministic video-track assignment (spec section 5, step 13).

Sort placed clips by start (tie-break by name, then clip_id); put each on the lowest video track
whose last clip ends at or before its start. All arithmetic is in integer ticks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .timebase import TICKS_PER_SECOND


@dataclass(frozen=True)
class Span:
    key: str            # clip_id
    name: str
    start_ticks: int
    end_ticks: int


def span_for(clip_id: str, name: str, start_ticks: int, duration_s: float, source_in_s: float,
             rate: Optional[float]) -> Span:
    """Timeline extent of a placed clip. A rate-r clip is laid out with its song-time length
    (duration * r) so it will not overlap neighbours once the editor conforms it."""
    r = rate or 1.0
    length_s = max(0.0, duration_s - source_in_s) * r
    end = start_ticks + int(round(length_s * TICKS_PER_SECOND))
    return Span(clip_id, name, start_ticks, max(end, start_ticks + 1))


def assign_tracks(spans: Iterable[Span]) -> dict[str, int]:
    """Return {clip_id: video_track} with tracks numbered from 1 (V1)."""
    ordered = sorted(spans, key=lambda sp: (sp.start_ticks, sp.name, sp.key))
    track_ends: list[int] = []
    out: dict[str, int] = {}
    for sp in ordered:
        for i, end in enumerate(track_ends):
            if end <= sp.start_ticks:
                track_ends[i] = sp.end_ticks
                out[sp.key] = i + 1
                break
        else:
            track_ends.append(sp.end_ticks)
            out[sp.key] = len(track_ends)
    return out
