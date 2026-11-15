"""Ad first cut: match shoot footage to a script / storyboard and assemble it in script order.

Matching cascade (docs/AD_GUIDE.md, research summary in docs/EDIT_DESIGN.md "Ad mode"):
  1. take log (CSV from the script supervisor / camera report): clip -> shot, take, circled, note
  2. file names carrying shot and take ("03A_T2", "SH03A-TK02", "sc3_sh2_tk4", "3A-2")
  3. dialogue: each scripted line is aligned to the clips' transcripts (engine/stt.py) with
     RapidFuzz partial-ratio alignment; the word timestamps give the in and out points
  4. storyboard picture (MOS shots only): colour histogram + difference hash similarity between
     the board image and frames of the clip -- a SUGGESTION, marked as such on the timeline
Take ranking: circled > best line coverage (dialogue) > later take > picture quality > name.
Nothing is guessed silently: unmatched shots stay empty with a "MISSING" marker; suggestions and
every match carry their method and confidence.
"""

from __future__ import annotations

import csv
import math
import os
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .script import Script, ScriptShot
from .stt import Transcript, Word

AD_PLAN_REVISION = 1

# --------------------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------------------

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + number_words(n % 100))
    if n < 1_000_000:
        return number_words(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + number_words(n % 1000))
    return str(n)


def norm_words(text: str, language: str = "en") -> list[str]:
    """Lowercase words for matching. English: digits spelled out (the transcript says "60", the
    script "sixty"). Every language: accents folded (café = cafe), punctuation dropped, letters of
    any script kept (Devanagari, Arabic, Cyrillic, CJK ...); other languages keep digits as digits."""
    import unicodedata
    t = text.lower().replace("’", "'")
    english = (language or "en").lower() in ("en", "english")
    if english:
        t = t.replace("%", " percent").replace("&", " and ")
        t = re.sub(r"(\d),(\d{3})", r"\1\2", t)
        t = re.sub(r"\d+", lambda m: " " + number_words(int(m.group())) + " ", t)
    t = unicodedata.normalize("NFKD", t)
    # drop Latin accent marks only (combining marks after a-z); keep marks that are part of other scripts
    t = re.sub(r"(?<=[a-z])[\u0300-\u036f]+", "", t)
    t = unicodedata.normalize("NFC", t)
    # keep letters, marks (Devanagari/Arabic vowel signs are marks, not letters) and digits
    t = "".join(ch if ch == "'" or unicodedata.category(ch)[0] in "LMN" else " " for ch in t)
    return [w.strip("'") for w in t.split() if w.strip("'")]


# --------------------------------------------------------------------------------------
# Shot ids, file names, take log
# --------------------------------------------------------------------------------------

def shot_key(shot_id: str) -> str:
    """'Shot 03A' -> '3a', '3.2' -> '3-2', 'SC3 SH2' -> '3-2'."""
    s = shot_id.strip().lower()
    s = re.sub(r"\b(shot|scene|frame|panel|sc|sh|s)\s*#?\s*(?=\d)", " ", s)
    parts = re.findall(r"\d+[a-z]?", s)
    if not parts:
        return re.sub(r"[^a-z0-9]+", "", s)
    return "-".join(re.sub(r"^0+(?=\d)", "", p) for p in parts)


_TAKE = r"(?:t|tk|take)[\s_\-.]*0*(\d{1,3})(?!\d)"
_NAME_PATTERNS = [
    re.compile(r"s(?:c|cene)?[\s_\-.]*0*(\d{1,3})[\s_\-.]*sh(?:ot)?[\s_\-.]*0*(\d{1,3}[a-z]?)[\s_\-.]*" + _TAKE),
    re.compile(r"(?:^|[^a-z0-9])(?:sh(?:ot)?|s)?[\s_\-.]*0*(\d{1,3}[a-z]?)[\s_\-.]*" + _TAKE),
    re.compile(r"(?:^|[^a-z0-9])0*(\d{1,3}[a-z]?)[\s_\-.]+0*(\d{1,2})$"),  # "3A-2" (shot-take) at the end
]


def name_shot_take(name: str) -> list[tuple[str, Optional[int]]]:
    """Candidate (shot key, take) pairs from a clip's file name, most specific first."""
    base = os.path.splitext(os.path.basename(name))[0].lower()
    out: list[tuple[str, Optional[int]]] = []
    m = _NAME_PATTERNS[0].search(base)
    if m:
        out.append((f"{int(m.group(1))}-{shot_key(m.group(2))}", int(m.group(3))))
        out.append((shot_key(m.group(2)), int(m.group(3))))
    for p in _NAME_PATTERNS[1:]:
        m = p.search(base)
        if m:
            out.append((shot_key(m.group(1)), int(m.group(2))))
    seen, uniq = set(), []
    for k in out:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


@dataclass
class LogEntry:
    shot: str
    take: Optional[int]
    circled: bool
    note: str


def read_takelog(path: str) -> dict[str, LogEntry]:
    """CSV with columns clip/file/name, shot/scene, take, circled/good/circle/select, notes."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        return {}
    h = [c.strip().lower() for c in rows[0]]

    def col(*names):
        for n in names:
            if n in h:
                return h.index(n)
        return None
    ci, si, ti = col("clip", "file", "filename", "name", "clip name"), col("shot", "scene", "shot id", "shot_id"), col("take", "tk")
    gi, ni = col("circled", "circle", "good", "select", "selected", "print"), col("notes", "note", "comment", "comments")
    if ci is None or si is None:
        raise ValueError("take log needs a clip (file name) column and a shot column")
    out = {}
    for r in rows[1:]:
        if len(r) <= max(ci, si) or not r[ci].strip():
            continue
        take = None
        if ti is not None and ti < len(r):
            m = re.search(r"\d+", r[ti])
            take = int(m.group()) if m else None
        good = gi is not None and gi < len(r) and r[gi].strip().lower() in ("y", "yes", "true", "1", "x", "✓", "✔", "●", "circled", "good", "print", "select")
        key = os.path.splitext(os.path.basename(r[ci].strip()))[0].lower()
        out[key] = LogEntry(shot_key(r[si]), take, good, r[ni].strip() if ni is not None and ni < len(r) else "")
    return out


# --------------------------------------------------------------------------------------
# Dialogue alignment
# --------------------------------------------------------------------------------------

@dataclass
class LineMatch:
    score: float          # 0..100 RapidFuzz partial ratio
    start: float          # seconds in the clip (first word)
    end: float            # seconds (last word)
    words: int


def align_line(line: str, transcript: Transcript, min_score: float = 70.0, max_matches: int = 3,
               language: str = "en") -> list[LineMatch]:
    """Where a scripted line is spoken in a clip (several times for repeated takes in one clip)."""
    from rapidfuzz import fuzz
    target = norm_words(line, language)
    if not target or not transcript.words:
        return []
    # normalised transcript words keep a pointer to the source word (for times)
    toks: list[tuple[str, Word]] = []
    for w in transcript.words:
        for t in norm_words(w.text, language):
            toks.append((t, w))
    if not toks:
        return []
    out: list[LineMatch] = []
    used = [False] * len(toks)
    n = len(target)
    tgt = " ".join(target)
    for _ in range(max_matches):
        best = None
        for width in sorted({max(1, int(round(n * f))) for f in (0.8, 1.0, 1.2)}):
            for i in range(0, max(1, len(toks) - width + 1)):
                if any(used[i:i + width]):
                    continue
                cand = " ".join(t for t, _ in toks[i:i + width])
                sc = fuzz.ratio(tgt, cand)
                if best is None or sc > best[0] + 1e-9:
                    best = (sc, i, i + width)
        if best is None or best[0] < min_score:
            break
        sc, i0, i1 = best
        for k in range(i0, i1):
            used[k] = True
        start, end = refine_to_speech(toks[i0][1].start, toks[i1 - 1][1].end, transcript.speech,
                                      toks[i0 + 1][1].start if i1 - i0 > 1 else None,
                                      toks[i1 - 2][1].end if i1 - i0 > 1 else None)
        out.append(LineMatch(score=round(sc, 2), start=start, end=end, words=i1 - i0))
    return out


def refine_to_speech(start: float, end: float, speech: list[list[float]], second_start: Optional[float] = None,
                     before_last_end: Optional[float] = None, join_gap: float = 0.6) -> tuple[float, float]:
    """Pull a line's start/end onto the measured speech regions.

    Whisper stretches the first word back over leading silence and the last word over trailing
    silence, and with small models even inner words drift early. Regions of real sound are the
    anchor: the line starts at the first region that contains, or begins after, the second word's
    start; it ends with the region around the second-to-last word's end; each side extends to the
    neighbouring region only across gaps up to `join_gap` (pauses inside a line), so a cough before
    or a crew call after the line is not swallowed. One-word lines use the regions inside the span."""
    regs = [r for r in speech if r[1] > start and r[0] < end]
    if not regs:
        return start, end
    # start
    if second_start is not None:
        cand = [i for i, r in enumerate(regs) if r[1] > second_start]
        si = cand[0] if cand else 0
    else:
        si = 0
    while si > 0 and regs[si][0] - regs[si - 1][1] <= join_gap:
        si -= 1
    # end
    if before_last_end is not None:
        cand = [i for i, r in enumerate(regs) if r[1] >= before_last_end]
        ei = cand[0] if cand else len(regs) - 1
    else:
        ei = len(regs) - 1
    while ei + 1 < len(regs) and regs[ei + 1][0] - regs[ei][1] <= join_gap:
        ei += 1
    if ei < si:
        ei = si
    s0 = max(start, regs[si][0])
    e0 = min(end, regs[ei][1])
    return (round(s0, 3), round(e0, 3)) if e0 > s0 else (start, end)


# --------------------------------------------------------------------------------------
# Storyboard pictures
# --------------------------------------------------------------------------------------

def _grab(path: str, t: Optional[float], w: int = 64, h: int = 36) -> Optional[np.ndarray]:
    from .probe import find_tool, run_tool
    cmd = [find_tool("ffmpeg"), "-v", "error"]
    if t is not None:
        cmd += ["-ss", f"{max(0.0, t):.3f}"]
    cmd += ["-i", path, "-frames:v", "1", "-vf", f"scale={w}:{h}:force_original_aspect_ratio=disable,format=rgb24",
            "-f", "rawvideo", "-"]
    r = run_tool(cmd, timeout=60)
    if r.returncode != 0 or len(r.stdout) < w * h * 3:
        return None
    return np.frombuffer(r.stdout[: w * h * 3], dtype=np.uint8).reshape(h, w, 3).astype(np.float64) / 255.0


def picture_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """0..1: half colour histogram correlation, half difference-hash agreement (layout)."""
    def hist(x):
        q = np.clip((x * 4).astype(int), 0, 3)
        idx = q[..., 0] * 16 + q[..., 1] * 4 + q[..., 2]
        hh = np.bincount(idx.ravel(), minlength=64).astype(np.float64)
        return (hh - hh.mean()) / (hh.std() + 1e-9)
    ha, hb = hist(a), hist(b)
    corr = float(np.clip(np.mean(ha * hb), -1, 1))

    def dhash(x):
        y = 0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]
        ys = np.linspace(0, y.shape[0], 9).astype(int)
        xs = np.linspace(0, y.shape[1], 10).astype(int)
        g = np.array([[y[ys[r]:ys[r + 1], xs[c]:xs[c + 1]].mean() for c in range(9)] for r in range(8)])
        return (g[:, 1:] > g[:, :-1]).ravel()
    agree = float(np.mean(dhash(a) == dhash(b)))
    return round(0.5 * max(0.0, corr) + 0.5 * agree, 4)


# --------------------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------------------

class AdSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target_s: Optional[float] = Field(None, gt=0, le=600)   # 15 / 30 / 60 ...; None = script timing
    handle_before_s: float = 0.25    # design: breath before the first word
    handle_after_s: float = 0.40     # design: let the last word land
    mos_default_s: float = 2.5       # design: a MOS shot with no scripted duration
    min_shot_s: float = 1.0          # design: shortest shot when fitting a target length
    skip_head_s: float = 1.0         # design: skip the slate / "action" at the head of a take
    vo_words_per_s: float = 2.6      # design: VO read speed when no VO recording is given
    min_line_score: float = 70.0     # design: RapidFuzz ratio for "this is the line"
    board_threshold: float = 0.62    # design: picture similarity for a storyboard suggestion
    language: str = "en"             # dialogue language: "en", "auto" or a Whisper language code (hi, es, fr ...)
    use_board_suggestions: bool = True
    snap_to_beats: bool = True


class AdAlt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clip_id: str
    name: str
    take: Optional[int] = None
    method: str
    score: float = 0.0
    circled: bool = False


class AdShot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: int
    shot_id: str
    kind: str
    description: str = ""
    dialogue: str = ""
    status: str                    # matched | suggested | missing
    clip_id: Optional[str] = None
    name: Optional[str] = None
    take: Optional[int] = None
    circled: bool = False
    method: str = ""               # take log | file name | dialogue | storyboard picture
    confidence: str = ""           # high | medium | low
    line_score: Optional[float] = None
    src_in_s: float = 0.0
    src_out_s: float = 0.0
    start: int = 0                 # sequence frames
    end: int = 0
    src_in: int = 0                # clip in/out in sequence frames (xmeml convention)
    src_out: int = 0
    vo_in_s: Optional[float] = None   # VO recording segment for this shot
    vo_out_s: Optional[float] = None
    super_text: str = ""
    reason: str = ""
    alternates: list[AdAlt] = Field(default_factory=list)


class AdPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = AD_PLAN_REVISION
    settings: AdSettings
    script: str
    fps_num: int
    fps_den: int
    duration_frames: int
    shots: list[AdShot]
    music_path: Optional[str] = None
    vo_path: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)


@dataclass
class Clip:
    clip_id: str
    path: str
    name: str
    duration_s: float
    has_audio: bool = True
    has_video: bool = True


@dataclass
class _Cand:
    clip: Clip
    method: str
    take: Optional[int] = None
    circled: bool = False
    note: str = ""
    line: Optional[LineMatch] = None
    board: float = 0.0
    quality: float = 0.5

    def key(self):
        # circled first, then line coverage, then explicit (log/name) over inferred, later take, quality
        explicit = 1 if self.method in ("take log", "file name") else 0
        return (1 if self.circled else 0, round(self.line.score, 1) if self.line else -1, explicit,
                self.take or 0, round(self.quality, 3), self.board, self.clip.name)


def _frames(seconds: float, fps: Fraction) -> int:
    return int(math.floor(Fraction(seconds).limit_denominator(10**9) * fps + Fraction(1, 2)))


def build_ad_plan(script: Script, clips: list[Clip], fps: Fraction, settings: Optional[AdSettings] = None,
                  transcripts: Optional[dict[str, Transcript]] = None, takelog: Optional[dict[str, LogEntry]] = None,
                  vo: Optional[Transcript] = None, vo_path: Optional[str] = None, music_path: Optional[str] = None,
                  beats: Optional[list[float]] = None, quality: Optional[dict] = None) -> AdPlan:
    """`quality`: clip_id -> engine.edit.ShotInfo (optional shot analysis) for picking MOS windows."""
    st = settings or AdSettings()
    transcripts = transcripts or {}
    takelog = takelog or {}
    quality = quality or {}
    warnings: list[str] = list(script.warnings)
    keys = {shot_key(s.shot_id): s for s in script.shots}

    # 1-2. take log and file names
    by_shot: dict[str, list[_Cand]] = {k: [] for k in keys}
    claimed: set[str] = set()
    for c in clips:
        base = os.path.splitext(c.name)[0].lower()
        q = quality.get(c.clip_id)
        qv = q.quality if q is not None else 0.5
        if base in takelog:
            e = takelog[base]
            if e.shot in by_shot:
                by_shot[e.shot].append(_Cand(c, "take log", e.take, e.circled, e.note, quality=qv))
                claimed.add(c.clip_id)
                continue
            warnings.append(f"take log: {c.name} is logged as shot {e.shot}, which is not in the script")
        for k, take in name_shot_take(c.name):
            if k in by_shot:
                by_shot[k].append(_Cand(c, "file name", take, quality=qv))
                claimed.add(c.clip_id)
                break

    # 3. dialogue: align every dialogue line against every transcript
    for k, s in keys.items():
        if s.kind != "dialogue" or not s.dialogue.strip():
            continue
        existing = {cd.clip.clip_id: cd for cd in by_shot[k]}
        for c in clips:
            t = transcripts.get(c.clip_id)
            if t is None or not t.words:
                continue
            ms = align_line(s.dialogue, t, st.min_line_score, language=st.language)
            if not ms:
                continue
            best = max(ms, key=lambda m: (m.score, m.start))  # the last good delivery wins ties
            if c.clip_id in existing:
                existing[c.clip_id].line = best
            elif c.clip_id not in claimed or not existing:
                q = quality.get(c.clip_id)
                by_shot[k].append(_Cand(c, "dialogue", None, line=best, quality=q.quality if q is not None else 0.5))

    # 4. storyboard pictures for MOS shots still without a candidate
    if st.use_board_suggestions:
        free = [c for c in clips if c.clip_id not in claimed and c.has_video]
        frames_cache: dict[str, list[np.ndarray]] = {}
        for k, s in keys.items():
            if by_shot[k] or not s.board or s.kind == "dialogue" or not os.path.isfile(s.board):
                continue
            board = _grab(s.board, None)
            if board is None:
                warnings.append(f"shot {s.shot_id}: storyboard picture not readable ({s.board})")
                continue
            best = None
            for c in free:
                if c.clip_id not in frames_cache:
                    frames_cache[c.clip_id] = [f for f in (_grab(c.path, c.duration_s * p) for p in (0.3, 0.5, 0.7)) if f is not None]
                sims = [picture_similarity(board, f) for f in frames_cache[c.clip_id]]
                sim = max(sims) if sims else 0.0
                if sim >= st.board_threshold and (best is None or sim > best.board):
                    best = _Cand(c, "storyboard picture", board=sim)
            if best is not None:
                by_shot[k].append(best)

    # Choose takes and trim
    plan_shots: list[AdShot] = []
    for s in script.shots:
        k = shot_key(s.shot_id)
        cands = sorted(by_shot.get(k, []), key=lambda cd: cd.key(), reverse=True)
        if s.kind == "dialogue":  # a dialogue shot needs its line found in the take, or an explicit log/name match
            cands = [cd for cd in cands if cd.line is not None or cd.method in ("take log", "file name")]
        shot = AdShot(order=s.order, shot_id=s.shot_id, kind=s.kind, description=s.description, dialogue=s.dialogue,
                      status="missing", super_text=s.super_text)
        alts = [AdAlt(clip_id=cd.clip.clip_id, name=cd.clip.name, take=cd.take, method=cd.method,
                      score=cd.line.score if cd.line else cd.board, circled=cd.circled) for cd in cands[1:4]]
        if cands:
            cd = cands[0]
            c = cd.clip
            shot.clip_id, shot.name, shot.take, shot.circled, shot.method = c.clip_id, c.name, cd.take, cd.circled, cd.method
            shot.status = "suggested" if cd.method == "storyboard picture" else "matched"
            shot.alternates = alts
            if cd.line is not None:
                shot.line_score = cd.line.score
                shot.src_in_s = max(0.0, cd.line.start - st.handle_before_s)
                shot.src_out_s = min(c.duration_s, cd.line.end + st.handle_after_s)
                shot.confidence = "high" if cd.line.score >= 85 and cd.method in ("take log", "file name") else \
                    "medium" if cd.line.score >= 85 or cd.method in ("take log", "file name") else "low"
                shot.reason = f"line found ({cd.line.score:.0f}%)" + (", circled take" if cd.circled else "")
            else:
                shot.confidence = "low" if cd.method == "storyboard picture" else ("high" if cd.circled else "medium")
                shot.reason = f"{cd.method}" + (f" take {cd.take}" if cd.take else "") + (", circled" if cd.circled else "") + \
                    (f", picture similarity {cd.board:.2f} (check)" if cd.method == "storyboard picture" else "")
        else:
            shot.reason = "no take found for this shot"
        plan_shots.append(shot)

    # Durations (seconds) per shot
    def wanted(s: ScriptShot, a: AdShot) -> tuple[float, bool]:
        """(seconds, fixed). Dialogue lines and recorded VO lines are never shortened."""
        if a.kind == "dialogue" and a.line_score is not None:
            return a.src_out_s - a.src_in_s, True
        if a.kind == "vo" and vo is not None:
            ms = align_line(s.dialogue, vo, st.min_line_score, language=st.language)
            if ms:
                m = ms[0]
                a.vo_in_s, a.vo_out_s = max(0.0, m.start - 0.1), m.end + 0.25
                return max(s.target_s or 0.0, a.vo_out_s - a.vo_in_s + 0.3), True
            warnings.append(f"shot {s.shot_id}: VO line not found in the VO recording")
        if a.kind == "vo" and s.dialogue:
            est = len(norm_words(s.dialogue, st.language)) / st.vo_words_per_s + 0.5
            return max(s.target_s or 0.0, est), False
        return s.target_s or st.mos_default_s, False

    durs, fixed = [], []
    for s, a in zip(script.shots, plan_shots):
        d, f = wanted(s, a)
        durs.append(d)
        fixed.append(f)
    total = sum(durs)
    if st.target_s:
        flex = [i for i, f in enumerate(fixed) if not f]
        fixed_total = sum(d for d, f in zip(durs, fixed) if f)
        flex_total = sum(durs[i] for i in flex)
        room = st.target_s - fixed_total
        if flex and flex_total > 0:
            # Scale the adjustable shots to the room; shots that hit the minimum stay there and the
            # rest are rescaled, until the total fits (or every adjustable shot is at the minimum).
            free = list(flex)
            base = {i: durs[i] for i in flex}
            for _ in range(len(flex) + 1):
                pinned = [i for i in flex if i not in free]
                left = room - st.min_shot_s * len(pinned)
                tot = sum(base[i] for i in free)
                if not free or tot <= 0:
                    break
                scale = left / tot
                newly = [i for i in free if base[i] * scale < st.min_shot_s]
                for i in free:
                    durs[i] = max(st.min_shot_s, base[i] * scale)
                for i in pinned:
                    durs[i] = st.min_shot_s
                if not newly:
                    break
                free = [i for i in free if i not in newly]
        if abs(sum(durs) - st.target_s) > 0.05:
            warnings.append(f"script runs {sum(durs):.2f} s against a target of {st.target_s:g} s "
                            f"(dialogue/VO lines are never cut; {len(flex)} adjustable shots at {st.min_shot_s:g} s minimum)")

    # Frames, beat snapping of adjustable boundaries
    # Round the running total, not each shot: the cut then lands on the target to the frame
    # (broadcast spots are exact), and no shot drifts by more than one frame.
    bounds = [0]
    acc = 0.0
    for d in durs:
        acc += d
        bounds.append(max(bounds[-1] + 1, _frames(acc, fps)))
    if beats and st.snap_to_beats:
        # Move a cut onto the nearest beat of the music bed (within 1/3 s) when the shot after it is
        # adjustable. Fixed shots (dialogue, recorded VO) are never shortened and the total length
        # never changes; both neighbours keep at least min_shot_s.
        beat_f = sorted({_frames(b, fps) for b in beats})
        min_f = max(1, _frames(st.min_shot_s, fps))
        tol = int(round(float(fps) / 3))
        for i in range(1, len(bounds) - 1):
            if fixed[i]:
                continue
            near = min(beat_f, key=lambda b: abs(b - bounds[i]), default=None)
            if near is None or abs(near - bounds[i]) > tol:
                continue
            delta = near - bounds[i]
            if fixed[i - 1] and delta < 0:
                continue
            if (bounds[i] - bounds[i - 1]) + delta < min_f or (bounds[i + 1] - bounds[i]) - delta < min_f:
                continue
            bounds[i] = near
    for i, a in enumerate(plan_shots):
        a.start, a.end = bounds[i], bounds[i + 1]
        n = a.end - a.start
        if a.status == "missing" or a.clip_id is None:
            continue
        clip = next(c for c in clips if c.clip_id == a.clip_id)
        total_f = max(1, _frames(clip.duration_s, fps))
        if a.line_score is not None and a.kind == "dialogue":
            src_in = _frames(a.src_in_s, fps)
        else:
            q = quality.get(a.clip_id)
            head = min(_frames(st.skip_head_s, fps), max(0, total_f - n))
            src_in = head
            if q is not None and q.times:  # best-looking window after the head
                step = max(1, int(round(float(fps) / 2)))
                best = (-1.0, head)
                for p in range(head, max(head, total_f - n) + 1, step):
                    qq = q.window(p / float(fps), (p + n) / float(fps))[0]
                    if qq > best[0] + 1e-9:
                        best = (qq, p)
                src_in = best[1]
        if src_in + n > total_f:
            src_in = max(0, total_f - n)
            if n > total_f:
                warnings.append(f"shot {a.shot_id}: take {a.name} is shorter than the shot ({total_f} < {n} frames); it ends early")
                a.end = a.start + total_f
                n = total_f
        a.src_in, a.src_out = src_in, src_in + n
        a.src_in_s, a.src_out_s = round(src_in / float(fps), 3), round((src_in + n) / float(fps), 3)
    duration = bounds[-1]
    plan = AdPlan(settings=st, script=script.source, fps_num=fps.numerator, fps_den=fps.denominator,
                  duration_frames=duration, shots=plan_shots, music_path=music_path, vo_path=vo_path,
                  warnings=warnings)
    plan.stats = {
        "shots": len(plan_shots),
        "matched": sum(1 for a in plan_shots if a.status == "matched"),
        "suggested": sum(1 for a in plan_shots if a.status == "suggested"),
        "missing": [a.shot_id for a in plan_shots if a.status == "missing"],
        "seconds": round(duration / float(fps), 3),
        "target_s": st.target_s,
        "by_method": {m: sum(1 for a in plan_shots if a.method == m) for m in ("take log", "file name", "dialogue", "storyboard picture")},
        "circled_used": sum(1 for a in plan_shots if a.circled),
    }
    return plan


def validate_ad_plan(plan: AdPlan, clips: list[Clip]) -> list[str]:
    problems = []
    fps = Fraction(plan.fps_num, plan.fps_den)
    by_id = {c.clip_id: c for c in clips}
    cursor = 0
    for a in plan.shots:
        if a.start != cursor:
            problems.append(f"shot {a.shot_id}: starts at {a.start}, expected {cursor}")
        if a.end <= a.start:
            problems.append(f"shot {a.shot_id}: empty")
        cursor = a.end
        if a.status != "missing":
            c = by_id.get(a.clip_id)
            if c is None:
                problems.append(f"shot {a.shot_id}: unknown clip")
                continue
            if a.src_in < 0 or a.src_out > max(1, _frames(c.duration_s, fps)) or a.src_out - a.src_in != a.end - a.start:
                problems.append(f"shot {a.shot_id}: source range {a.src_in}-{a.src_out} invalid for {c.name}")
    if cursor != plan.duration_frames:
        problems.append("shots do not end at the sequence end")
    order = [a.order for a in plan.shots]
    if order != sorted(order):
        problems.append("shots are not in script order")
    return problems



# --------------------------------------------------------------------------------------
# Cutdowns (30 -> 15 -> 6)
# --------------------------------------------------------------------------------------

_HERO = re.compile(r"\b(packshot|pack shot|product|logo|end ?card|hero|title|cta|call to action)\b", re.I)


def cutdown_script(script: Script, length: float, full: AdPlan, fps: Fraction, settings: Optional[AdSettings] = None) -> tuple[Script, list[str], str]:
    """The shots of a shorter version. When the script marks shots for this length ("in 15"
    column), exactly those. Otherwise: the end card / packshot, the spoken lines, the opening
    shot, then picture-only shots in script order while they fit at their minimum length.
    Returns (script, dropped shot ids, how they were chosen)."""
    st = settings or AdSettings()
    L = int(round(length))
    marked = [s for s in script.shots if s.versions]
    if marked:
        keep = [s for s in script.shots if L in s.versions]
        if keep:
            return (script.model_copy(update={"shots": [s.model_copy(update={"order": i + 1}) for i, s in enumerate(keep)]}),
                    [s.shot_id for s in script.shots if s not in keep], f"script columns (in {L})")
    planned = {a.shot_id: a for a in full.shots}
    secs = {s.shot_id: (planned[s.shot_id].end - planned[s.shot_id].start) / float(fps) if s.shot_id in planned else st.mos_default_s
            for s in script.shots}
    fixed = {s.shot_id for s in script.shots if s.kind in ("dialogue", "vo") and s.dialogue}

    def need(s):  # time a shot needs in the cutdown: lines keep their length, picture can shrink
        return secs[s.shot_id] if s.shot_id in fixed else st.min_shot_s

    last = script.shots[-1]
    prio = []
    for i, s in enumerate(script.shots):
        p = 0
        if s is last and (s.kind in ("graphic", "vo") or _HERO.search(s.description + " " + s.notes)):
            p = 100
        elif _HERO.search(s.description + " " + s.notes) and s.kind != "dialogue":
            p = 85
        elif s.shot_id in fixed:
            p = 80 - i * 0.01
        elif i == 0:
            p = 70
        else:
            p = 50 - i * 0.01
        prio.append((p, i, s))
    chosen, used = set(), 0.0
    for p, i, s in sorted(prio, key=lambda x: (-x[0], x[1])):
        if used + need(s) <= length + 1e-6:
            chosen.add(i)
            used += need(s)
    keep = [s for i, s in enumerate(script.shots) if i in chosen]
    return (script.model_copy(update={"shots": [s.model_copy(update={"order": k + 1}) for k, s in enumerate(keep)]}),
            [s.shot_id for i, s in enumerate(script.shots) if i not in chosen], "automatic (end card, lines, opening, then picture)")
