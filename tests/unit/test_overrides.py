from engine.overrides import OverrideError, apply_override, placeable
from engine.result import ClipResult, MasterInfo, SequenceInfo, Status, SyncResult
from engine.timebase import TICKS_PER_SECOND as T

import pytest


def _clip(cid, start_s, track, status=Status.HIGH, dur=10.0):
    return ClipResult(clip_id=cid, path=f"/{cid}.mov", name=cid, duration_s=dur, has_audio=True, status=status,
                      song_position_s=start_s, song_position_ticks=str(int(start_s * 25) * T // 25), video_track=track, rate=1.0)


def _result():
    return SyncResult(engine_version="t", created_at="x", master=MasterInfo(path="/m.wav", sha256="0" * 64, duration_s=100, sample_rate=48000),
                      sequence=SequenceInfo(fps_num=25, fps_den=1), settings={},
                      clips=[_clip("a", 0, 1), _clip("b", 20, 1), _clip("c", 5, 2)])


def test_override_keeps_current_track_when_free():
    r = _result()
    c = apply_override(r, "a", 10.0, "ed")   # 10..20 is free on V1 (b starts at 20)
    assert c.override.video_track == 1
    assert c.song_position_s == 0 and c.video_track == 1   # engine fields untouched


def test_override_moves_to_lowest_free_track_on_overlap():
    r = _result()
    c = apply_override(r, "a", 15.0, "ed")   # overlaps b on V1 and c on V2 (5..15 ends at 15 -> free)
    assert c.override.video_track == 2
    c = apply_override(r, "a", 12.0, "ed")   # overlaps b on V1 and c on V2
    assert c.override.video_track == 3


def test_override_validation_and_clear():
    r = _result()
    with pytest.raises(OverrideError):
        apply_override(r, "a", -1.0, "ed")
    with pytest.raises(OverrideError):
        apply_override(r, "zzz", 1.0, "ed")
    apply_override(r, "a", 10.0, "ed")
    assert apply_override(r, "a", None, "ed").override is None


def test_placeable_rules():
    low = _clip("l", 0, 3, Status.LOW)
    assert not placeable(low) and placeable(low, place_low=True)
    r = _result()
    r.clips.append(low)
    apply_override(r, "l", 50.0, "ed")
    assert placeable(r.find("l"))
