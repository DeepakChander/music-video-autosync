"""Manual overrides. They never touch engine fields: the correction goes in `clip.override`, and
the track for the overridden position is chosen by the engine's layout rules (lowest video track
with no overlap against every other clip's effective span)."""

from __future__ import annotations

import datetime as _dt
from fractions import Fraction
from typing import Optional

from .layout import Span, span_for
from .result import ClipResult, Override, Status, SyncResult
from .timebase import snap_seconds_to_frame_ticks


class OverrideError(ValueError):
    pass


def effective_track(c: ClipResult) -> Optional[int]:
    return c.override.video_track if c.override is not None else c.video_track


def _span(c: ClipResult, start_ticks: int) -> Span:
    return span_for(c.clip_id, c.name, start_ticks, c.duration_s, c.source_in_s if c.override is None else 0.0, c.rate)


def lowest_free_track(result: SyncResult, clip: ClipResult, start_ticks: int) -> int:
    """The clip's current track if the new span is free there (the panel can then apply the
    correction as a plain move), otherwise the lowest free track."""
    me = span_for(clip.clip_id, clip.name, start_ticks, clip.duration_s, 0.0, clip.rate)
    busy: dict[int, list[Span]] = {}
    for c in result.clips:
        if c.clip_id == clip.clip_id:
            continue
        t = effective_track(c)
        pos = c.effective_position_ticks
        if t is None or pos is None:
            continue
        busy.setdefault(t, []).append(_span(c, pos))
    def free(t: int) -> bool:
        return all(sp.end_ticks <= me.start_ticks or sp.start_ticks >= me.end_ticks for sp in busy.get(t, []))

    current = effective_track(clip)
    if current is not None and free(current):
        return current
    track = 1
    while not free(track):
        track += 1
    return track


def apply_override(result: SyncResult, clip_id: str, song_position_s: Optional[float], by: str,
                   note: Optional[str] = None, at: Optional[str] = None) -> ClipResult:
    """Set (or clear, with song_position_s=None) the manual position of one clip.

    The overridden clip is placed from its first frame (no pre-roll trim): a manual position is
    where the editor wants the clip's first frame. Negative positions are rejected.
    """
    clip = result.find(clip_id)
    if clip is None:
        raise OverrideError(f"unknown clip_id {clip_id}")
    if song_position_s is None:
        clip.override = None
        return clip
    if song_position_s < 0:
        raise OverrideError("song_position_s must be >= 0 (the sequence starts at the song start)")
    if not by or not by.strip():
        raise OverrideError("'by' is required")
    fps = Fraction(result.sequence.fps_num, result.sequence.fps_den)
    ticks, _err = snap_seconds_to_frame_ticks(song_position_s, fps)
    track = lowest_free_track(result, clip, ticks)
    clip.override = Override(
        song_position_s=round(float(song_position_s), 6), song_position_ticks=str(ticks), by=by.strip(),
        at=at or _dt.datetime.now().astimezone().isoformat(timespec="seconds"), video_track=track, note=note,
    )
    return clip


def placeable(c: ClipResult, place_low: bool = False) -> bool:
    """Which clips go on the timeline: HIGH, MEDIUM, any override, and LOW only when asked
    (docs/DECISIONS.md D-010). UNSYNCED clips are never placed unless overridden."""
    if c.override is not None:
        return True
    if c.status in (Status.HIGH, Status.MEDIUM):
        return True
    return place_low and c.status == Status.LOW
