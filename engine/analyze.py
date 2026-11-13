"""The analysis pipeline: master context, per-clip analysis, caching, process pool, layout.

The engine is the only writer of sync positions (spec section 3). It writes nothing except
sync_result.json and its cache directory.
"""

from __future__ import annotations

import concurrent.futures as cf
import datetime as _dt
import hashlib
import json
import math
import os
import threading
import traceback
from dataclasses import dataclass
from fractions import Fraction
from typing import Callable, Iterable, Optional

import numpy as np

from . import ALGORITHM_REVISION, __version__
from .confidence import tier
from .decode import DecodeError, decode_mono, rms_dbfs
from .features import apply_stats, feature_stats, features, raw_features, stretch
from .layout import assign_tracks, fill_gaps, span_for
from .locate import Candidate, MasterRateView, coarse_candidates, peak_ratio, refine, rescore
from .probe import MediaInfo, ProbeError, probe
from .result import (Alternate, ClipResult, Confidence, Diagnostics, Drift, MasterInfo, Reason,
                     SequenceInfo, Status, Summary, SyncResult)
from .settings import EngineSettings
from .timebase import parse_rate, seconds_to_ticks, snap_seconds_to_frame_ticks
from .validate import validate_windows

MEDIA_EXTENSIONS = {
    ".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".mts", ".m2ts", ".mpg", ".mpeg", ".webm",
    ".wav", ".mp3", ".aac", ".m4a", ".aif", ".aiff", ".flac", ".ogg", ".wmv", ".3gp", ".r3d", ".braw",
}

ProgressFn = Callable[[dict], None]


class MasterError(RuntimeError):
    pass


def _r(x: Optional[float], nd: int = 6) -> Optional[float]:
    if x is None:
        return None
    v = round(float(x), nd)
    return 0.0 if v == 0 else v  # no "-0.0" in the JSON


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def clip_identity(path: str) -> tuple[str, str]:
    """Stable clip id = hash of absolute path + size + mtime (spec section 4)."""
    ap = os.path.abspath(path)
    try:
        st = os.stat(ap)
        sig = f"{ap}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        sig = f"{ap}|missing"
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16], ap


def list_media(folder: str) -> list[str]:
    out = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if f.startswith("."):
                continue
            if os.path.splitext(f)[1].lower() in MEDIA_EXTENSIONS:
                out.append(os.path.join(root, f))
    return sorted(out, key=lambda p: (p.casefold(), p))


# --------------------------------------------------------------------------------------
# Master
# --------------------------------------------------------------------------------------

@dataclass
class MasterContext:
    info: MasterInfo
    audio: np.ndarray
    views: dict[float, MasterRateView]

    @property
    def duration_s(self) -> float:
        return self.info.duration_s


def master_cache_file(cache_dir: str, sha: str, s: EngineSettings) -> str:
    key = hashlib.sha256((f"{sha}|a{ALGORITHM_REVISION}|" + "|".join(s.feature_hash(r) for r in s.rates)).encode()).hexdigest()[:24]
    return os.path.join(cache_dir, "master", f"{key}.npz")


def load_master(path: str, s: EngineSettings, cache_dir: Optional[str] = None) -> MasterContext:
    """Decode and analyse the master song once; cache features on disk keyed by file hash."""
    if not os.path.isfile(path):
        raise MasterError(f"master not found: {path}")
    try:
        mi = probe(path)
    except ProbeError as e:
        raise MasterError(f"cannot read master: {e}") from e
    if not mi.has_audio:
        raise MasterError("master has no audio stream")
    sha = sha256_file(path)
    cache_file = master_cache_file(cache_dir, sha, s) if cache_dir else None
    audio = None
    feats: dict[float, np.ndarray] = {}
    stats: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    if cache_file and os.path.isfile(cache_file):
        try:
            with np.load(cache_file) as z:
                audio = z["audio"]
                for r in s.rates:
                    feats[r] = z[f"feats_{r:g}"]
                    stats[r] = (z[f"mu_{r:g}"], z[f"sd_{r:g}"])
        except Exception:
            audio, feats, stats = None, {}, {}
    if audio is None or len(feats) != len(s.rates):
        try:
            audio = decode_mono(path, s.analysis_sr)
        except DecodeError as e:
            raise MasterError(f"cannot decode master: {e}") from e
        # The master is never time-stretched: rate r only narrows its feature band.
        for r in s.rates:
            raw = raw_features(audio, s, rate=r)
            stats[r] = feature_stats(raw)
            feats[r] = apply_stats(raw, *stats[r], s)
        if cache_file:
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            tmp = cache_file + ".tmp.npz"
            arrays = {f"feats_{r:g}": f for r, f in feats.items()}
            for r, (mu, sd) in stats.items():
                arrays[f"mu_{r:g}"], arrays[f"sd_{r:g}"] = mu, sd
            np.savez(tmp, audio=audio, **arrays)
            os.replace(tmp, cache_file)
    duration = audio.size / s.analysis_sr
    info = MasterInfo(path=os.path.abspath(path), sha256=sha, duration_s=_r(duration, 6),
                      sample_rate=int(mi.audio_sample_rate or s.analysis_sr))
    views = {r: MasterRateView.build(r, feats[r], *stats[r]) for r in s.rates}
    return MasterContext(info=info, audio=audio, views=views)


def waveform_peaks(audio: np.ndarray, bins: int = 600) -> list[float]:
    if audio.size == 0:
        return [0.0] * bins
    edges = np.linspace(0, audio.size, bins + 1).astype(int)
    peaks = [float(np.max(np.abs(audio[a:b]))) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])]
    m = max(peaks) or 1.0
    return [round(p / m, 4) for p in peaks]


# --------------------------------------------------------------------------------------
# One clip
# --------------------------------------------------------------------------------------

def _unsynced(clip_id: str, path: str, name: str, reason: Reason, detail: str, duration: float = 0.0,
              info: Optional[MediaInfo] = None, has_audio: bool = False, rms: Optional[float] = None,
              conf: Optional[Confidence] = None, alts: Optional[list[Alternate]] = None) -> ClipResult:
    return ClipResult(
        clip_id=clip_id, path=path, name=name, duration_s=_r(duration, 4) or 0.0,
        source_fps=info.video_fps if info else None, has_audio=has_audio,
        status=Status.UNSYNCED, reason=reason, reason_detail=detail,
        confidence=conf or Confidence(), alternates=alts or [],
        diagnostics=Diagnostics(audio_rms_dbfs=_r(rms, 2), av_offset_s=_r(info.av_offset_s if info else 0.0, 6) or 0.0,
                                audio_channels=info.audio_channels if info else 0),
    )


def analyze_clip(path: str, master: MasterContext, s: EngineSettings, seq_fps: Fraction) -> ClipResult:
    clip_id, ap = clip_identity(path)
    name = os.path.splitext(os.path.basename(ap))[0]
    try:
        info = probe(ap)
    except ProbeError as e:
        return _unsynced(clip_id, ap, name, Reason.DECODE_ERROR, f"ffprobe: {e}")
    except Exception as e:  # timeouts, permission errors
        return _unsynced(clip_id, ap, name, Reason.DECODE_ERROR, f"probe failed: {e}")
    duration = info.duration_s
    if not info.has_audio:
        return _unsynced(clip_id, ap, name, Reason.NO_AUDIO, "no audio stream", duration, info)
    try:
        audio = decode_mono(ap, s.analysis_sr)
    except DecodeError as e:
        return _unsynced(clip_id, ap, name, Reason.DECODE_ERROR, f"ffmpeg: {e}", duration, info, True)
    audio_dur = audio.size / s.analysis_sr
    if duration <= 0:
        duration = audio_dur
    rms = rms_dbfs(audio)
    if audio_dur < s.min_clip_s:
        return _unsynced(clip_id, ap, name, Reason.TOO_SHORT,
                         f"audio is {audio_dur:.2f} s; minimum is {s.min_clip_s:g} s", duration, info, True, rms)
    if rms < s.silence_dbfs:
        return _unsynced(clip_id, ap, name, Reason.NO_AUDIO,
                         f"audio stream is silent ({rms:.1f} dBFS)", duration, info, True, rms)

    stretched: dict[float, np.ndarray] = {}
    feats: dict[float, np.ndarray] = {}
    cands: list[Candidate] = []
    for rate in s.rates:
        y = stretch(audio, rate)
        f = features(y, s, rate)
        stretched[rate], feats[rate] = y, f
        cands.extend(coarse_candidates(f, master.views[rate], s))
    if not cands:
        return _unsynced(clip_id, ap, name, Reason.NO_MATCH, "no overlap with the song", duration, info, True, rms)
    # Fine-align every candidate, then rescore it on the master's audio at its exact position so
    # that exact repeats compare on equal terms (see locate.rescore).
    for c in cands:
        refine(c, master.audio, stretched[c.rate], s)
        c.coarse_score = c.score
        c.score = rescore(c, feats[c.rate], master.audio, master.views[c.rate], s)
    cands.sort(key=lambda c: (-c.score, c.rate, c.lag))
    best = cands[0]
    ratio, distinct = peak_ratio(best, cands, s)
    if ratio < s.ambiguity_ratio:
        # Exact repeats score equal up to float noise, which differs between CPUs. Report the
        # earliest near-tied position so the (LOW, not placed by default) result is the same on
        # every machine; the others stay in alternates.
        ties = [c for c in cands if c.score >= best.score / s.tie_ratio]
        chosen = min(ties, key=lambda c: (round(c.best_position_s, 3), c.rate))
        if chosen is not best:
            best = chosen
            _ratio, distinct = peak_ratio(best, cands, s)

    seq_frame_s = float(1 / seq_fps)
    tol = s.agreement_tolerance_frames * seq_frame_s
    song_len_s = feats[best.rate].shape[0] * s.hop_length / s.analysis_sr
    short_clip = song_len_s < s.window_min_clip_s
    wrep = validate_windows(best, feats[best.rate], stretched[best.rate], master.views[best.rate],
                            master.audio, master.duration_s, s, tol)
    agreement = wrep.agreement
    status, reason = tier(best.score, ratio, agreement, s, short_clip=short_clip or wrep.evaluated == 0,
                          fine_used=best.fine_used)

    av = info.av_offset_s
    alternates = sorted(
        (Alternate(song_position_s=_r(c.best_position_s - av * c.rate, 6), score=_r(c.score, 4), rate=c.rate)
         for c in distinct if c.score >= s.noise_floor),
        key=lambda a: (-a.score, a.rate, a.song_position_s))
    conf = Confidence(score=_r(best.score, 4), peak_ratio=_r(ratio, 4),
                      window_agreement=_r(agreement, 4) if agreement is not None else None)
    if status == Status.UNSYNCED:
        return _unsynced(clip_id, ap, name, reason or Reason.NO_MATCH,
                         f"best score {best.score:.3f} is below the noise floor {s.noise_floor:g}",
                         duration, info, True, rms, conf, alternates)

    # Song position of the clip's first video frame (audio sample 0 sits av seconds later).
    raw = best.best_position_s - av * best.rate
    source_in_s = 0.0
    pos = raw
    if raw < 0:
        # Pre-roll policy "set_in_point": trim the head on the source frame grid so the
        # song lines up; full media stays available as handles.
        src_fps = parse_rate(info.video_fps) if info.video_fps else seq_fps
        frames_in = math.ceil((-raw / best.rate) * float(src_fps) - 1e-9)
        source_in_s = float(Fraction(frames_in) / src_fps)
        pos = raw + source_in_s * best.rate
    ticks, snap_err = snap_seconds_to_frame_ticks(pos, seq_fps)
    detail = None
    if reason == Reason.AMBIGUOUS:
        detail = (f"{len(alternates)} other position(s) score within {s.ambiguity_ratio:g}x; "
                  "pick one from alternates")
    drift = None
    if wrep.drift_ppm is not None:
        reliable = (wrep.drift_residual_ms or 0.0) <= s.drift_max_residual_ms
        drift = Drift(ppm=_r(wrep.drift_ppm, 1) if reliable else None,
                      max_error_ms=_r(wrep.drift_max_error_ms, 3), windows=wrep.drift_windows,
                      residual_ms=_r(wrep.drift_residual_ms, 3) or 0.0, reliable=reliable)
    return ClipResult(
        clip_id=clip_id, path=ap, name=name, duration_s=_r(duration, 4),
        source_fps=info.video_fps, has_audio=True, status=status, reason=reason, reason_detail=detail,
        song_position_s=_r(pos, 6), song_position_ticks=str(ticks),
        source_in_s=_r(source_in_s, 6) or 0.0, source_in_ticks=str(seconds_to_ticks(source_in_s)),
        rate=best.rate, confidence=conf, alternates=alternates, drift=drift,
        diagnostics=Diagnostics(raw_song_position_s=_r(raw, 6), av_offset_s=_r(av, 6) or 0.0,
                                fine_peak=_r(best.fine_peak, 4), fine_used=best.fine_used,
                                audio_rms_dbfs=_r(rms, 2), frame_snap_error_s=_r(snap_err, 6),
                                windows_evaluated=wrep.evaluated, audio_channels=info.audio_channels),
    )


# --------------------------------------------------------------------------------------
# Worker process plumbing
# --------------------------------------------------------------------------------------

_W: dict = {}


def _init_worker(master_path: str, settings_json: str, cache_dir: Optional[str], fps: str) -> None:
    s = EngineSettings.model_validate_json(settings_json)
    _W["s"] = s
    _W["master"] = load_master(master_path, s, cache_dir)
    _W["fps"] = parse_rate(fps)


def _work(path: str) -> str:
    try:
        res = analyze_clip(path, _W["master"], _W["s"], _W["fps"])
    except Exception as e:  # never lose a clip: report it
        cid, ap = clip_identity(path)
        res = _unsynced(cid, ap, os.path.splitext(os.path.basename(ap))[0], Reason.ANALYSIS_ERROR,
                        f"analysis failed: {type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")
    return res.model_dump_json()


# --------------------------------------------------------------------------------------
# Whole run
# --------------------------------------------------------------------------------------

def clip_cache_key(clip_id: str, master_sha: str, s: EngineSettings, fps: Fraction) -> str:
    blob = f"{clip_id}|{master_sha}|{s.settings_hash()}|{__version__}|a{ALGORITHM_REVISION}|{fps.numerator}/{fps.denominator}"
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _counts(results: Iterable[ClipResult]) -> dict:
    c = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNSYNCED": 0}
    for r in results:
        c[r.status.value] += 1
    return c


def apply_layout(clips: list[ClipResult]) -> int:
    """Returns the number of video tracks used by the clips placed by default (HIGH/MEDIUM)."""
    def sp(c: ClipResult):
        return span_for(c.clip_id, c.name, int(c.song_position_ticks), c.duration_s, c.source_in_s, c.rate)
    primary = [c for c in clips if c.status in (Status.HIGH, Status.MEDIUM)]
    secondary = [c for c in clips if c.status == Status.LOW and c.song_position_ticks is not None]
    tracks = assign_tracks(sp(c) for c in primary)
    existing: dict[int, list] = {}
    for c in primary:
        existing.setdefault(tracks[c.clip_id], []).append(sp(c))
    tracks.update(fill_gaps((sp(c) for c in secondary), existing))
    for c in clips:
        c.video_track = tracks.get(c.clip_id)
    return max((tracks[c.clip_id] for c in primary), default=0)


def analyze(master_path: str, clip_paths: list[str], seq: SequenceInfo, s: EngineSettings,
            cache_dir: Optional[str] = None, workers: Optional[int] = None,
            progress: Optional[ProgressFn] = None, cancel: Optional[threading.Event] = None,
            created_at: Optional[str] = None) -> SyncResult:
    fps = Fraction(seq.fps_num, seq.fps_den)
    master = load_master(master_path, s, cache_dir)  # also fills the master cache for workers

    # De-duplicate, keep a deterministic order.
    unique: dict[str, str] = {}
    for p in clip_paths:
        cid, ap = clip_identity(p)
        unique.setdefault(ap, cid)
    paths = sorted(unique, key=lambda p: (p.casefold(), p))
    total = len(paths)
    results: dict[str, ClipResult] = {}
    cache_hits = 0

    def emit(current: Optional[str], event: str) -> None:
        if progress:
            progress({"event": event, "total": total, "done": len(results), "current": current,
                      "cache_hits": cache_hits, **_counts(results.values())})

    clip_cache_dir = os.path.join(cache_dir, "clips") if cache_dir else None
    todo: list[str] = []
    for p in paths:
        cid = unique[p]
        cached = None
        if clip_cache_dir:
            cf_path = os.path.join(clip_cache_dir, clip_cache_key(cid, master.info.sha256, s, fps) + ".json")
            if os.path.isfile(cf_path):
                try:
                    with open(cf_path, "r", encoding="utf-8") as f:
                        cached = ClipResult.model_validate_json(f.read())
                except Exception:
                    cached = None
        if cached is not None:
            results[p] = cached
            cache_hits += 1
        else:
            todo.append(p)
    emit(None, "start")

    def store(p: str, res: ClipResult) -> None:
        res.video_track = None
        res.override = None
        results[p] = res
        if clip_cache_dir:
            os.makedirs(clip_cache_dir, exist_ok=True)
            key = clip_cache_key(unique[p], master.info.sha256, s, fps)
            tmp = os.path.join(clip_cache_dir, key + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(res.model_dump_json())
            os.replace(tmp, os.path.join(clip_cache_dir, key + ".json"))
        emit(res.name, "clip")

    n_workers = workers or s.max_workers or max(1, (os.cpu_count() or 2) - 1)
    n_workers = max(1, min(n_workers, len(todo) or 1))
    if todo and n_workers == 1:
        for p in todo:
            if cancel is not None and cancel.is_set():
                break
            store(p, ClipResult.model_validate_json(_work_inline(p, master, s, fps)))
    elif todo:
        settings_json = s.model_dump_json()
        with cf.ProcessPoolExecutor(max_workers=n_workers, initializer=_init_worker,
                                    initargs=(master_path, settings_json, cache_dir, f"{fps.numerator}/{fps.denominator}")) as ex:
            futs = {ex.submit(_work, p): p for p in todo}
            try:
                for fut in cf.as_completed(futs):
                    p = futs[fut]
                    try:
                        store(p, ClipResult.model_validate_json(fut.result()))
                    except cf.CancelledError:
                        continue
                    if cancel is not None and cancel.is_set():
                        for f2 in futs:
                            f2.cancel()
            finally:
                if cancel is not None and cancel.is_set():
                    ex.shutdown(wait=True, cancel_futures=True)

    # Every input clip appears in the result. Clips skipped by a cancel are NOT_ANALYZED.
    for p in paths:
        if p not in results:
            cid = unique[p]
            results[p] = _unsynced(cid, p, os.path.splitext(os.path.basename(p))[0], Reason.NOT_ANALYZED,
                                   "run was cancelled before this clip was analysed")
    clips = [results[p] for p in paths]
    n_tracks = apply_layout(clips)
    counts = _counts(clips)
    created = created_at or _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    result = SyncResult(
        engine_version=__version__, created_at=created, master=master.info, sequence=seq,
        settings=s.public_dict(), clips=clips,
        summary=Summary(total=total, video_tracks=n_tracks, **counts),
    )
    emit(None, "done")
    return result


def _work_inline(path: str, master: MasterContext, s: EngineSettings, fps: Fraction) -> str:
    _W.update({"s": s, "master": master, "fps": fps})
    return _work(path)


def write_result(result: SyncResult, out_path: str) -> None:
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(result.to_json())
    os.replace(tmp, out_path)


def result_without_timestamps(result: SyncResult) -> str:
    d = json.loads(result.to_json())
    d.pop("created_at", None)
    return json.dumps(d, sort_keys=True)
