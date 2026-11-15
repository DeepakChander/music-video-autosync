"""Edit planner rules (docs/EDIT_DESIGN.md section 3) on hand-built sync results + a real music map."""

from fractions import Fraction

import numpy as np
import pytest
from scipy.signal import resample_poly

from engine.edit import EditSettings, build_plan, seconds_to_frames, takes_from_result, validate_plan
from engine.music import analyse_music
from engine.result import ClipResult, Diagnostics, MasterInfo, Reason, SequenceInfo, Status, SyncResult
from engine.settings import EngineSettings
from engine.timebase import snap_seconds_to_frame_ticks
from tools.synth_song import make_song

FPS = Fraction(24000, 1001)


@pytest.fixture(scope="module")
def song():
    s = make_song(seed=1, bpm=112.0, layout="pop")
    mm = analyse_music(resample_poly(s.audio, 1, 3).astype(np.float32), EngineSettings())
    return s, mm


def clip(cid, start_s=None, dur_s=None, status=Status.HIGH, rate=1.0, preroll_s=0.0):
    if start_s is None:  # unsynced story clip
        return ClipResult(clip_id=cid, path=f"/shoot/{cid}.mov", name=f"{cid}.mov", duration_s=dur_s,
                          source_fps="24000/1001", has_audio=False, status=Status.UNSYNCED, reason=Reason.NO_AUDIO)
    # A take whose audio starts `preroll_s` before the song: placed at 0 with an in-point.
    raw = start_s - preroll_s
    ticks, _ = snap_seconds_to_frame_ticks(max(0.0, raw), FPS)
    return ClipResult(clip_id=cid, path=f"/shoot/{cid}.mov", name=f"{cid}.mov", duration_s=dur_s,
                      source_fps="24000/1001", has_audio=True, status=status, song_position_s=max(0.0, raw),
                      song_position_ticks=str(ticks), source_in_s=preroll_s, rate=rate, video_track=1,
                      diagnostics=Diagnostics(raw_song_position_s=raw))


def result(clips, duration):
    return SyncResult(engine_version="test", created_at="t", settings={},
                      master=MasterInfo(path="/shoot/song.wav", sha256="x", duration_s=duration, sample_rate=48000),
                      sequence=SequenceInfo(fps_num=24000, fps_den=1001, width=1920, height=1080), clips=clips)


def full_song_shoot(duration, n=6, story=0):
    cl = [clip(f"take{i:02d}", 0.0, duration + 2.0) for i in range(1, n + 1)]
    cl += [clip(f"story{i:02d}", None, 40.0) for i in range(1, story + 1)]
    return result(cl, duration)


def test_full_song_takes_follow_every_rule(song):
    s, mm = song
    res = full_song_shoot(s.duration_s)
    plan = build_plan(res, mm, EditSettings(style="performance"))
    assert validate_plan(plan, res, mm) == []
    assert plan.stats["gap_frames"] == 0
    starts = {sh.start for sh in plan.shots}
    for sec in mm.sections:
        assert seconds_to_frames(sec.start_s, FPS) in starts, "every section start is a cut"
    assert plan.stats["takes_used"] == 6
    usage = plan.stats["seconds_by_take"].values()
    assert max(usage) < 3 * min(usage), "screen time is balanced across takes"


def test_choruses_cut_faster_than_verses_and_hype_faster_than_ballad(song):
    s, mm = song
    res = full_song_shoot(s.duration_s)
    perf = build_plan(res, mm, EditSettings(style="performance")).stats["mean_shot_s_by_section"]
    if "chorus" in perf and "verse" in perf:
        assert perf["chorus"] < perf["verse"]
    hype = build_plan(res, mm, EditSettings(style="hype")).stats["mean_shot_s"]
    ballad = build_plan(res, mm, EditSettings(style="ballad")).stats["mean_shot_s"]
    assert hype < ballad
    # research range: mean shot 2.1-4.8 s for performance-style videos
    assert 1.0 <= build_plan(res, mm, EditSettings(style="performance")).stats["mean_shot_s"] <= 6.0


def test_deterministic_and_seed_gives_a_variation(song):
    s, mm = song
    res = full_song_shoot(s.duration_s)
    a = build_plan(res, mm, EditSettings(seed=0)).model_dump_json()
    b = build_plan(res, mm, EditSettings(seed=0)).model_dump_json()
    c = build_plan(res, mm, EditSettings(seed=1)).model_dump_json()
    assert a == b
    assert a != c


def test_story_share_order_and_never_on_a_chorus_downbeat(song):
    s, mm = song
    res = full_song_shoot(s.duration_s, n=5, story=4)
    plan = build_plan(res, mm, EditSettings(style="performance_story"))
    assert validate_plan(plan, res, mm) == []
    story = [sh for sh in plan.shots if sh.kind == "story"]
    share = sum(sh.end - sh.start for sh in story) / plan.duration_frames
    assert 0.2 <= share <= 0.45
    chorus_starts = {seconds_to_frames(x.start_s, FPS) for x in mm.sections if x.kind == "chorus"}
    assert not any(sh.start in chorus_starts for sh in story)
    order = [int(sh.clip_id[-2:]) for sh in story]
    assert order == sorted(order), "story clips play in file order across the song"


def test_lip_sync_with_preroll_and_partial_coverage(song):
    s, mm = song
    d = s.duration_s
    clips = [clip("wideA", 0.0, d + 1.0), clip("closeB", 0.0, d + 1.0, preroll_s=3.0),
             clip("partC", 30.0, 60.0), clip("partD", 100.0, 50.0, status=Status.MEDIUM)]
    res = result(clips, d)
    plan = build_plan(res, mm, EditSettings(style="hype"))
    assert validate_plan(plan, res, mm) == []
    takes = {t.clip.clip_id: t for t in takes_from_result(res, plan.settings)[0]}
    b = takes["closeB"]
    assert b.in_frame == seconds_to_frames(3.0, FPS)
    for sh in plan.shots:
        if sh.clip_id == "closeB":
            assert sh.src_in == b.in_frame + sh.start  # placed at frame 0 with a 3 s in-point


def test_uncovered_song_parts_get_story_footage_or_a_reported_gap(song):
    s, mm = song
    d = s.duration_s
    # With a story clip (a 2x take is story: not lip-synced at native speed), holes are filled.
    clips = [clip("A", 20.0, 60.0), clip("B", 20.0, 60.0), clip("slow", 0.0, 30.0, rate=2.0)]
    res = result(clips, d)
    plan = build_plan(res, mm, EditSettings(style="performance"))
    assert validate_plan(plan, res, mm) == []
    perf_ids = {t.clip.clip_id for t in takes_from_result(res, plan.settings)[0]}
    assert "slow" not in perf_ids
    assert plan.stats["gap_frames"] == 0 and plan.stats["story_shots"] > 0
    # Without story footage the holes stay empty and are reported, never filled with a guess.
    res2 = result(clips[:2], d)
    plan2 = build_plan(res2, mm, EditSettings(style="performance"))
    assert validate_plan(plan2, res2, mm) == []
    assert plan2.stats["gap_frames"] > 0 and plan2.warnings


def test_effects_by_style(song):
    s, mm = song
    res = full_song_shoot(s.duration_s)
    hype = build_plan(res, mm, EditSettings(style="hype"))
    kinds = {e.kind for sh in hype.shots for e in sh.effects}
    if any(x.kind == "chorus" for x in mm.sections):
        assert {"punch_in", "flash"} <= kinds
    none = build_plan(res, mm, EditSettings(style="hype", effects=False))
    assert not any(sh.effects for sh in none.shots)
    assert [m.name for m in hype.markers][0] in ("Intro", "Verse 1", "Section 1", "Chorus 1", "Pre Chorus 1", "Bridge 1")
