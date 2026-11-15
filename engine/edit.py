"""Edit planner: turns synced takes + the music map into a first cut (docs/EDIT_DESIGN.md).

Everything is in sequence frames (integers), so cut points, source in/out and markers are exact.
Lip sync is preserved structurally: a performance shot's source frame is always
    source_in_frame = take_in_frame + (shot_start_frame - take_start_frame)
which is the placement the synced draft already uses (export/fcp7_xml.py plan()).

Rules and their sources are listed in docs/EDIT_DESIGN.md section 1. The planner is
deterministic: the same inputs and `seed` give the same cut; a different seed gives a variation.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .music import MusicMap
from .result import ClipResult, Status, SyncResult
from .timebase import TICKS_PER_SECOND, ticks_per_frame

EDIT_PLAN_REVISION = 1

Style = Literal["performance", "performance_story", "hype", "ballad"]

# Base shot length in beats per section kind and style (research: ~1 beat is the most common shot
# length, ~1 bar the next; choruses faster than verses, bridges slower, ballads slowest).
_BASE_BEATS: dict[str, dict[str, float]] = {
    "performance":       {"intro": 8, "verse": 4, "pre-chorus": 2, "chorus": 2, "bridge": 8, "outro": 8, "section": 4},
    "performance_story": {"intro": 8, "verse": 4, "pre-chorus": 2, "chorus": 2, "bridge": 8, "outro": 8, "section": 4},
    "hype":              {"intro": 4, "verse": 2, "pre-chorus": 1, "chorus": 1, "bridge": 4, "outro": 4, "section": 2},
    "ballad":            {"intro": 8, "verse": 8, "pre-chorus": 4, "chorus": 4, "bridge": 8, "outro": 8, "section": 8},
}
_STORY_SHARE = {"performance": 0.0, "performance_story": 0.3, "hype": 0.15, "ballad": 0.25}
# How suitable each section kind is for story footage (research: story in verses/breaks).
_STORY_AFFINITY = {"intro": 1.0, "verse": 0.8, "bridge": 1.0, "outro": 0.8, "section": 0.6, "pre-chorus": 0.3, "chorus": 0.1}
_MARKER_COLOUR = {"chorus": "red", "verse": "blue", "pre-chorus": "yellow", "bridge": "cyan",
                  "intro": "green", "outro": "green", "section": "orange"}


class EditSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    style: Style = "performance"
    pace: float = Field(1.0, gt=0.2, le=4.0)        # >1 = more cuts
    story_share: Optional[float] = Field(None, ge=0.0, le=0.8)  # None = style default
    early_frames: int = Field(0, ge=0, le=6)         # cut this many frames before the beat
    seed: int = 0
    effects: bool = True                              # style effects (punch-ins, flashes, dissolves)
    place_low: bool = False
    hero_clip_id: Optional[str] = None
    story_clip_ids: list[str] = Field(default_factory=list)    # synced clips the editor marks as story
    exclude_clip_ids: list[str] = Field(default_factory=list)
    max_one_beat_run: int = 4                         # research: no long runs of 1-beat cuts


class Effect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["punch_in", "flash", "dissolve"]
    frames: int                    # duration
    amount: float = 0.0            # punch_in: scale percent at the end (e.g. 110)


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    start: int                     # sequence frame (inclusive)
    end: int                       # sequence frame (exclusive)
    kind: Literal["performance", "story", "gap"]
    clip_id: Optional[str] = None
    src_in: int = 0                # clip in-point in sequence frames (xmeml convention)
    src_out: int = 0               # exclusive
    section: int = 0
    reason: str = ""
    alternates: list[str] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list)


class Marker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int
    frames: int
    name: str
    colour: str
    comment: str = ""


class EditPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = EDIT_PLAN_REVISION
    settings: EditSettings
    fps_num: int
    fps_den: int
    duration_frames: int
    shots: list[Shot]
    markers: list[Marker]
    warnings: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Takes
# --------------------------------------------------------------------------------------

def seconds_to_frames(seconds: float, fps: Fraction) -> int:
    """Nearest frame, half up (same rule as the XML exporter)."""
    return int(math.floor(Fraction(seconds).limit_denominator(10**9) * fps + Fraction(1, 2)))


@dataclass
class Take:
    clip: ClipResult
    start: int            # sequence frame where the take's in-point sits
    in_frame: int         # take in-point (sequence frames)
    total: int            # take length (sequence frames)
    story: bool = False

    @property
    def end(self) -> int:  # first sequence frame after the take's last frame
        return self.start + (self.total - self.in_frame)

    def covers(self, a: int, b: int) -> bool:
        return self.start <= a and b <= self.end


def takes_from_result(result: SyncResult, settings: EditSettings) -> tuple[list[Take], list[Take]]:
    """(performance takes, story clips). Performance = synced 1x video takes (HIGH/MEDIUM, any
    override, LOW only when asked). Story = video clips with no song position (UNSYNCED), slow-motion
    takes, and clips the editor marks as story."""
    fps = Fraction(result.sequence.fps_num, result.sequence.fps_den)
    tpf = ticks_per_frame(fps)
    perf: list[Take] = []
    story: list[Take] = []
    excluded = set(settings.exclude_clip_ids)
    marked = set(settings.story_clip_ids)
    for c in result.clips:
        if c.clip_id in excluded or not c.source_fps:
            continue
        total = max(1, seconds_to_frames(c.duration_s, fps))
        synced = c.override is not None or c.status in (Status.HIGH, Status.MEDIUM) or (
            settings.place_low and c.status == Status.LOW)
        one_x = c.override is not None or abs((c.rate or 1.0) - 1.0) < 1e-9
        if synced and one_x and c.clip_id not in marked:
            if c.override is not None:
                ticks, in_s = int(c.override.song_position_ticks), 0.0
            else:
                ticks, in_s = int(c.song_position_ticks), 0.0
                raw = c.diagnostics.raw_song_position_s
                if c.source_in_s > 0 and raw is not None:
                    in_s = (ticks / TICKS_PER_SECOND - raw) / (c.rate or 1.0)
            start = Fraction(ticks) / tpf
            if start.denominator != 1:
                continue  # not on a frame: the sync result guarantees this never happens
            in_f = min(total - 1, max(0, seconds_to_frames(in_s, fps)))
            perf.append(Take(c, int(start), in_f, total))
        elif c.status == Status.UNSYNCED or not one_x or c.clip_id in marked:
            story.append(Take(c, 0, 0, total, story=True))
    perf.sort(key=lambda t: t.clip.name.lower())
    story.sort(key=lambda t: t.clip.name.lower())  # shoot order usually follows file names
    return perf, story


# --------------------------------------------------------------------------------------
# Grid and pacing
# --------------------------------------------------------------------------------------

@dataclass
class GridPoint:
    frame: int
    beat: bool
    downbeat: bool
    section_start: bool
    section: int
    energy: Optional[float] = None   # loudness of the beat starting here (0..1)


def build_grid(music: MusicMap, fps: Fraction, duration_frames: int, early: int) -> list[GridPoint]:
    sec_starts = {seconds_to_frames(s.start_s, fps): s.index for s in music.sections}
    downs = {seconds_to_frames(t, fps) for t in music.downbeats}
    pts: dict[int, GridPoint] = {}
    energies = music.beat_energy if len(music.beat_energy) == len(music.beats) else [None] * len(music.beats)
    for t, e in zip(music.beats, energies):
        f = seconds_to_frames(t, fps)
        f = max(0, f - early) if f > 0 else 0
        if 0 <= f < duration_frames:
            pts[f] = GridPoint(f, True, (f + early) in downs or f in downs, False, 0, e)
    for f0, idx in sec_starts.items():
        f = max(0, f0 - early) if f0 > 0 else 0
        if f < duration_frames:
            g = pts.get(f) or GridPoint(f, False, True, True, idx)
            g.section_start, g.section = True, idx
            pts[f] = g
    pts.setdefault(0, GridPoint(0, False, True, True, 0))
    pts[0].section_start = True
    ordered = sorted(pts.values(), key=lambda g: g.frame)
    cur = 0
    for g in ordered:  # carry the section index forward
        if g.section_start:
            cur = g.section
        g.section = cur
    return ordered


def _rng(seed: int, *parts) -> float:
    h = hashlib.sha256(("|".join(str(p) for p in (seed,) + parts)).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def shot_beats(base: float, energy: float, pace: float) -> float:
    """Energy shortens shots (louder bars cut faster); pace scales everything."""
    b = base * (2.0 ** (0.6 * (0.5 - energy))) / pace
    return max(1.0, 2.0 ** round(math.log2(max(b, 1.0))))


def plan_slots(grid: list[GridPoint], music: MusicMap, settings: EditSettings, duration_frames: int) -> list[tuple[int, int, int]]:
    """[(start_frame, end_frame, section_index)]: shot boundaries on beats, a forced cut at every
    section start, lengths varied around the section's base length."""
    base_table = _BASE_BEATS[settings.style]
    sections = {s.index: s for s in music.sections}
    beats_f = [g for g in grid]
    slots: list[tuple[int, int, int]] = []
    i = 0
    one_beat_run = 0
    n = len(beats_f)
    while i < n:
        g = beats_f[i]
        sec = sections.get(g.section)
        kind = sec.kind if sec else "section"
        # Loudness of this bar (mean of its beats), else the section's.
        bar = [p.energy for p in beats_f[i: i + music.beats_per_bar] if p.energy is not None]
        e = sum(bar) / len(bar) if bar else (sec.energy if sec else 0.5)
        base = shot_beats(base_table.get(kind, 4), e, settings.pace)
        # Research: the mode is short (about a beat) but means are 2-5 s, so mix short shots with
        # occasional holds: half 20%, base 45%, double 25%, quadruple 10%.
        r = _rng(settings.seed, "len", g.frame)
        mult = 0.5 if r < 0.2 else 1.0 if r < 0.65 else 2.0 if r < 0.9 else 4.0
        want = max(1, int(round(base * mult)))
        if want == 1 and one_beat_run >= settings.max_one_beat_run:
            want = 2
        # Advance `want` grid steps, but stop at the next section start.
        j = i + 1
        steps = 1
        while j < n and steps < want and not beats_f[j].section_start:
            j += 1
            steps += 1
        # Shots of a bar or longer end on a downbeat when one is within reach.
        if want >= music.beats_per_bar and j < n and not beats_f[j].downbeat and not beats_f[j].section_start:
            for d in (1, -1, 2, -2):
                k = j + d
                if i < k < n and beats_f[k].downbeat and all(not beats_f[q].section_start for q in range(i + 1, k)):
                    j = k
                    break
        end = beats_f[j].frame if j < n else duration_frames
        slots.append((g.frame, end, g.section))
        one_beat_run = one_beat_run + 1 if steps == 1 else 0
        i = j
    return [s for s in slots if s[1] > s[0]]


# --------------------------------------------------------------------------------------
# Choosing takes
# --------------------------------------------------------------------------------------

@dataclass
class ShotInfo:
    """Optional per-take analysis (engine/shots.py). Missing info = neutral scores."""
    quality: float = 0.5            # 0..1: face visible, sharp, well exposed
    size: Optional[str] = None      # wide | medium | close
    setup: Optional[str] = None     # camera setup group; takes in one setup never cut together


@dataclass
class _State:
    prev: Optional[str] = None
    prev_setup: Optional[str] = None
    prev_size: Optional[str] = None
    recent: list[str] = field(default_factory=list)
    used: dict[str, int] = field(default_factory=dict)
    chorus_setups: set[str] = field(default_factory=set)
    verse_setups: set[str] = field(default_factory=set)
    assigned: int = 0


def _score(t: Take, a: int, b: int, kind: str, section_start: bool, info: ShotInfo, st: _State,
           settings: EditSettings, n_takes: int, shot_index: int) -> Optional[float]:
    cid = t.clip.clip_id
    setup = info.setup or cid
    if cid == st.prev:
        return None                                   # never the same take twice in a row
    s = info.quality
    s += 0.1 if t.clip.status == Status.HIGH else 0.0
    if st.prev_setup is not None and setup == st.prev_setup:
        s -= 0.6                                      # same camera setup = jump cut
    if info.size and st.prev_size and info.size != st.prev_size:
        s += 0.15                                     # alternate shot sizes
    if section_start and info.size == "wide" and kind in ("intro", "verse", "bridge"):
        s += 0.1
    # Recently used takes wait a little (graded, so the order never settles into a fixed cycle).
    if st.recent[-2:-1] == [cid]:
        s -= 0.2
    elif st.recent[-3:-2] == [cid]:
        s -= 0.08
    fair = max(1.0, st.assigned / max(1, n_takes))
    s -= 0.3 * max(0.0, st.used.get(cid, 0) / fair - 1.0)  # balance screen time
    if kind == "chorus" and setup in st.chorus_setups:
        s += 0.15                                     # choruses come back to their setup
    if kind == "verse" and st.verse_setups and setup not in st.verse_setups:
        s += 0.1                                      # new setup per verse
    if settings.hero_clip_id == cid and section_start:
        s += 0.1
    s += 0.15 * _rng(settings.seed, "pick", shot_index, cid)  # deterministic variation
    return s


def build_plan(result: SyncResult, music: MusicMap, settings: Optional[EditSettings] = None,
               shot_info: Optional[dict[str, ShotInfo]] = None) -> EditPlan:
    settings = settings or EditSettings()
    shot_info = shot_info or {}
    fps = Fraction(result.sequence.fps_num, result.sequence.fps_den)
    duration = max(1, seconds_to_frames(result.master.duration_s, fps))
    perf, story = takes_from_result(result, settings)
    warnings: list[str] = []
    if not perf:
        warnings.append("No synced 1x video takes: the cut uses story clips only.")
    grid = build_grid(music, fps, duration, settings.early_frames)
    slots = plan_slots(grid, music, settings, duration)
    sections = {s.index: s for s in music.sections}
    section_start_frames = {g.frame for g in grid if g.section_start}

    # Story slots: highest story affinity first, until the share of the song is reached.
    share = settings.story_share if settings.story_share is not None else _STORY_SHARE[settings.style]
    story_slots: set[int] = set()
    if story and share > 0:
        def affinity(k: int) -> float:
            a, b, si = slots[k]
            sec = sections.get(si)
            kind = sec.kind if sec else "section"
            e = sec.energy if sec else 0.5
            return _STORY_AFFINITY.get(kind, 0.5) * (1.0 - 0.5 * e) + 0.01 * _rng(settings.seed, "story", k)
        target = share * duration
        got = 0
        for k in sorted(range(len(slots)), key=affinity, reverse=True):
            a, b, si = slots[k]
            sec = sections.get(si)
            if sec and sec.kind == "chorus" and a in section_start_frames:
                continue  # a chorus downbeat always gets performance
            if got >= target:
                break
            story_slots.add(k)
            got += b - a

    # Story order: spread story clips across their slots in file order; each use takes the next
    # part of the clip so the story progresses.
    story_order = sorted(story_slots)
    story_pos: dict[str, int] = {}

    st = _State()
    shots: list[Shot] = []
    work = [(a, b, si, k, False) for k, (a, b, si) in enumerate(slots)]
    w = 0
    while w < len(work):
        a, b, si, k, is_rest = work[w]
        w += 1
        sec = sections.get(si)
        kind = sec.kind if sec else "section"
        is_start = a in section_start_frames and not is_rest
        cands = [t for t in perf if t.covers(a, b)]
        if (k in story_slots and not is_rest) or not cands:
            if story:
                idx = story_order.index(k) if k in story_slots else 0
                t = story[min(len(story) - 1, idx * len(story) // max(1, len(story_order)))] if k in story_slots else story[k % len(story)]
                length = b - a
                pos = story_pos.get(t.clip.clip_id, min(t.total // 10, max(0, t.total - length)))
                if pos + length > t.total:
                    pos = max(0, t.total - length)
                if length <= t.total:
                    story_pos[t.clip.clip_id] = pos + length
                    shots.append(Shot(index=len(shots), start=a, end=b, kind="story", clip_id=t.clip.clip_id,
                                      src_in=pos, src_out=pos + length, section=si,
                                      reason="story slot" if k in story_slots else "no synced take covers this beat"))
                    st.prev, st.prev_setup = t.clip.clip_id, None
                    continue
            if not cands:
                # No take covers the whole shot: use the take that covers the longest start of it
                # and give the rest to the next take (an off-beat cut forced by coverage).
                partial = [t for t in perf if t.start <= a < t.end and t.clip.clip_id != st.prev] or \
                          [t for t in perf if t.start <= a < t.end]
                if partial:
                    t = max(partial, key=lambda t: (t.end, t.clip.clip_id))
                    cands = [t]
                    if t.end < b:
                        work.insert(w, (t.end, b, si, k, True))
                        b = t.end
                else:
                    later = [t.start for t in perf if a < t.start < b]
                    if later:  # a take starts inside this shot: gap until then, take after
                        work.insert(w, (min(later), b, si, k, True))
                        b = min(later)
                    shots.append(Shot(index=len(shots), start=a, end=b, kind="gap", section=si,
                                      reason="no take covers this part of the song"))
                    warnings.append(f"gap at frames {a}-{b}: no take covers it")
                    continue
        scored = []
        for t in cands:
            info = shot_info.get(t.clip.clip_id, ShotInfo())
            sc = _score(t, a, b, kind, is_start, info, st, settings, len(perf), k)
            if sc is not None:
                scored.append((sc, t.clip.clip_id, t))
        if not scored:  # only the previous take covers this slot
            scored = [(0.0, cands[0].clip.clip_id, cands[0])]
        scored.sort(key=lambda x: (-x[0], x[1]))
        best = scored[0][2]
        info = shot_info.get(best.clip.clip_id, ShotInfo())
        src_in = best.in_frame + (a - best.start)
        last = shots[-1] if shots else None
        if (last is not None and last.kind == "performance" and last.clip_id == best.clip.clip_id
                and last.end == a and last.src_out == src_in):
            # Only the previous take covers this beat: a cut to the same synced take would be
            # invisible, so the shot simply continues.
            last.end, last.src_out = b, src_in + (b - a)
            st.used[best.clip.clip_id] = st.used.get(best.clip.clip_id, 0) + (b - a)
            st.assigned += b - a
            continue
        shots.append(Shot(index=len(shots), start=a, end=b, kind="performance", clip_id=best.clip.clip_id,
                          src_in=src_in, src_out=src_in + (b - a), section=si,
                          reason=f"{kind}{' section start' if is_start else ''}{' coverage cut' if is_rest else ''}; score {scored[0][0]:.2f}",
                          alternates=[x[1] for x in scored[1:4]]))
        cid = best.clip.clip_id
        st.prev, st.prev_setup, st.prev_size = cid, info.setup or cid, info.size
        st.recent = (st.recent + [cid])[-3:]
        st.used[cid] = st.used.get(cid, 0) + (b - a)
        st.assigned += b - a
        if kind == "chorus":
            st.chorus_setups.add(info.setup or cid)
        if kind == "verse":
            st.verse_setups.add(info.setup or cid)

    # Merge a slot that was shortened (partial cover) with following gaps is not needed: slots are
    # contiguous by construction; fill any hole left by shortening with a gap marker.
    fixed: list[Shot] = []
    cursor = 0
    for s in shots:
        if s.start > cursor:
            fixed.append(Shot(index=0, start=cursor, end=s.start, kind="gap", section=s.section, reason="uncovered"))
        fixed.append(s)
        cursor = max(cursor, s.end)
    if cursor < duration:
        fixed.append(Shot(index=0, start=cursor, end=duration, kind="gap", section=fixed[-1].section if fixed else 0,
                          reason="uncovered tail"))
    for i, s in enumerate(fixed):
        s.index = i

    if settings.effects:
        _add_effects(fixed, music, sections, settings, fps)
    markers = _markers(music, fps, duration)
    plan = EditPlan(settings=settings, fps_num=result.sequence.fps_num, fps_den=result.sequence.fps_den,
                    duration_frames=duration, shots=fixed, markers=markers, warnings=warnings)
    plan.stats = plan_stats(plan, music, fps)
    return plan


def _add_effects(shots: list[Shot], music: MusicMap, sections: dict, settings: EditSettings, fps: Fraction) -> None:
    fpsf = float(fps)
    starts = {seconds_to_frames(s.start_s, fps): s for s in music.sections}
    for i, s in enumerate(shots):
        if s.kind == "gap":
            continue
        sec = sections.get(s.section)
        kind = sec.kind if sec else "section"
        energy = sec.energy if sec else 0.5
        if settings.style == "hype" and kind == "chorus" and s.end - s.start >= int(0.5 * fpsf):
            s.effects.append(Effect(kind="punch_in", frames=max(2, int(round(0.15 * fpsf))), amount=110.0))
        if settings.style in ("hype", "performance", "performance_story") and s.start in starts and kind == "chorus":
            s.effects.append(Effect(kind="flash", frames=max(2, int(round(0.08 * fpsf)))))
        if settings.style == "ballad" and energy < 0.45 and i > 0 and shots[i - 1].kind != "gap":
            s.effects.append(Effect(kind="dissolve", frames=max(4, int(round(0.4 * fpsf)))))


def _markers(music: MusicMap, fps: Fraction, duration: int) -> list[Marker]:
    counts: dict[str, int] = {}
    out = []
    for s in music.sections:
        counts[s.kind] = counts.get(s.kind, 0) + 1
        title = s.kind.replace("-", " ").title()
        name = f"{title} {counts[s.kind]}" if s.kind not in ("intro", "outro") else title
        a = seconds_to_frames(s.start_s, fps)
        b = min(duration, seconds_to_frames(s.end_s, fps))
        out.append(Marker(start=a, frames=max(1, b - a), name=name, colour=_MARKER_COLOUR.get(s.kind, "orange"),
                          comment=f"label {s.label}, {s.bars} bars, energy {s.energy:.2f}"))
    return out


def plan_stats(plan: EditPlan, music: MusicMap, fps: Fraction) -> dict:
    fpsf = float(fps)
    perf = [s for s in plan.shots if s.kind == "performance"]
    by_kind: dict[str, list[float]] = {}
    secs = {s.index: s for s in music.sections}
    for s in plan.shots:
        if s.kind == "gap":
            continue
        k = secs[s.section].kind if s.section in secs else "section"
        by_kind.setdefault(k, []).append((s.end - s.start) / fpsf)
    usage: dict[str, float] = {}
    for s in plan.shots:
        if s.clip_id:
            usage[s.clip_id] = usage.get(s.clip_id, 0.0) + (s.end - s.start) / fpsf
    return {
        "shots": len(plan.shots),
        "performance_shots": len(perf),
        "story_shots": sum(1 for s in plan.shots if s.kind == "story"),
        "gap_frames": sum(s.end - s.start for s in plan.shots if s.kind == "gap"),
        "mean_shot_s": round(sum((s.end - s.start) for s in plan.shots) / max(1, len(plan.shots)) / fpsf, 3),
        "mean_shot_s_by_section": {k: round(sum(v) / len(v), 3) for k, v in sorted(by_kind.items())},
        "takes_used": len(usage),
        "seconds_by_take": {k: round(v, 3) for k, v in sorted(usage.items())},
    }


def validate_plan(plan: EditPlan, result: SyncResult, music: MusicMap) -> list[str]:
    """Rule checks used by the tests and shown in the panel. Empty list = all rules hold."""
    problems = []
    fps = Fraction(plan.fps_num, plan.fps_den)
    perf, story = takes_from_result(result, plan.settings)
    takes = {t.clip.clip_id: t for t in perf}
    stories = {t.clip.clip_id: t for t in story}
    cursor = 0
    grid = {g.frame for g in build_grid(music, fps, plan.duration_frames, plan.settings.early_frames)}
    for i, s in enumerate(plan.shots):
        if s.start != cursor:
            problems.append(f"shot {i}: starts at {s.start}, expected {cursor} (hole or overlap)")
        cursor = s.end
        if s.end <= s.start:
            problems.append(f"shot {i}: empty")
        if s.start not in grid and s.kind != "gap" and "coverage cut" not in s.reason:
            problems.append(f"shot {i}: starts at frame {s.start}, not on a beat or section start")
        if i > 0 and s.clip_id and plan.shots[i - 1].clip_id == s.clip_id and s.kind == "performance":
            problems.append(f"shot {i}: same take as the previous shot ({s.clip_id})")
        if s.kind == "performance":
            t = takes.get(s.clip_id)
            if t is None:
                problems.append(f"shot {i}: {s.clip_id} is not a synced take")
                continue
            if s.src_in != t.in_frame + (s.start - t.start):
                problems.append(f"shot {i}: lip sync broken (src_in {s.src_in} != {t.in_frame + (s.start - t.start)})")
            if not t.covers(s.start, s.end):
                problems.append(f"shot {i}: take {s.clip_id} does not cover {s.start}-{s.end}")
        if s.kind == "story":
            t = stories.get(s.clip_id)
            if t is None or s.src_out > t.total or s.src_in < 0:
                problems.append(f"shot {i}: story clip range outside the clip")
    if cursor != plan.duration_frames:
        problems.append(f"cut ends at {cursor}, song ends at {plan.duration_frames}")
    return problems
