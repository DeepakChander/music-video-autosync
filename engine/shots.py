"""Shot analysis of video takes: faces and shot size, sharpness, exposure, motion, colour, setups.

Frames are sampled by FFmpeg (separate process): keyframes only when the codec allows it, held at
`sample_fps`, scaled to `width` with an explicit BT.709 matrix (untagged HD would otherwise be read
as BT.601). Faces: YuNet 2023mar (MIT, OpenCV model zoo, sha256 8f2383e4...) through OpenCV's
FaceDetectorYN; when OpenCV is not installed, face fields are empty and the report says so.

Per sample: luma mean / 5th / 95th percentile, clipped fractions, sharpness (log10 variance of the
Laplacian of luma), motion (mean absolute luma change from the previous sample), Lab statistics of
mid-tones, faces (count, largest face height / frame height, centre).
Per take: shot size by the median largest-face height (close / medium / wide; design thresholds,
tune on real footage), a 32x18 thumbnail signature for grouping takes into camera setups.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy import ndimage

from .colour import LabStats, lab_stats, luma, pool_stats
from .probe import ProbeError, find_tool, probe, run_tool

SHOTS_REVISION = 2  # 2: face (skin) colour statistics
MODEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "face_detection_yunet_2023mar.onnx")
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


class ShotSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sample_fps: float = 2.0         # design: 2 samples per second (analysis report: 2 fps fallback)
    width: int = 640                # analysis report: 640 px; faces in wide shots vanish at 320 px
    keyframes_only: bool = True     # analysis report: ~2.7 s per minute of 1080p H.264
    face_score: float = 0.7         # YuNet default score threshold
    close_face: float = 0.22        # design: largest face >= 22% of frame height = close-up
    wide_face: float = 0.08         # design: < 8% (or no face) = wide
    black_luma: float = 0.06        # 95th percentile luma below this = black frame
    setup_distance: float = 0.35    # design: 1 - correlation of setup thumbnails; below = same setup

    def key(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:12]


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t: float                       # seconds in the clip
    luma: float
    p05: float
    p95: float
    clip_lo: float
    clip_hi: float
    sharp: float                   # log10 Laplacian variance
    motion: float
    faces: int = 0
    face_h: float = 0.0            # largest face height / frame height
    face_x: float = 0.5
    face_y: float = 0.5
    lab: Optional[LabStats] = None
    face_lab: Optional[LabStats] = None   # inner part of the largest face (skin tones)


class TakeShots(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = SHOTS_REVISION
    clip_id: str
    path: str
    width: int
    height: int
    duration_s: float
    face_detector: str             # "yunet" | "none"
    samples: list[Sample]
    size: Optional[str] = None     # close | medium | wide (None = no picture)
    signature: list[float] = Field(default_factory=list)  # setup thumbnail (32x18 luma, normalised)
    colour: Optional[LabStats] = None
    face_colour: Optional[LabStats] = None
    error: Optional[str] = None


class ShotsReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = SHOTS_REVISION
    settings: ShotSettings
    takes: dict[str, TakeShots]
    setups: dict[str, str]         # clip_id -> setup name (S1, S2, ...)


# --------------------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------------------

def _rotation(info) -> int:
    for s in (info.raw.get("streams") or []):
        if s.get("codec_type") != "video":
            continue
        rot = (s.get("tags") or {}).get("rotate")
        for sd in s.get("side_data_list") or []:
            if "rotation" in sd:
                rot = sd["rotation"]
        try:
            return int(round(float(rot))) % 360 if rot is not None else 0
        except (TypeError, ValueError):
            return 0
    return 0


def sample_frames(path: str, ss: ShotSettings) -> tuple[np.ndarray, int, int, float]:
    """(frames uint8 (n, h, w, 3), w, h, duration). Raises ProbeError when there is no picture."""
    info = probe(path)
    if not info.has_video or not info.width or not info.height:
        raise ProbeError("no video stream")
    sw, sh = info.width, info.height
    if _rotation(info) in (90, 270):
        sw, sh = sh, sw
    w = ss.width
    h = max(2, int(round(w * sh / sw / 2)) * 2)
    vf = (f"fps={ss.sample_fps},scale={w}:{h}:in_color_matrix=bt709:out_color_matrix=bt709:flags=area,"
          "format=rgb24")
    ffmpeg = find_tool("ffmpeg")

    def run(keyframes: bool):
        cmd = [ffmpeg, "-v", "error", "-nostdin"]
        if keyframes:
            cmd += ["-skip_frame", "nokey"]
        cmd += ["-i", path, "-map", "0:v:0", "-an", "-sn", "-vf", vf, "-f", "rawvideo", "-"]
        r = run_tool(cmd, timeout=max(120.0, info.duration_s * 4))
        data = np.frombuffer(r.stdout, dtype=np.uint8)
        n = data.size // (w * h * 3)
        return r, data[: n * w * h * 3].reshape(n, h, w, 3)

    r, frames = run(ss.keyframes_only)
    expected = int(info.duration_s * ss.sample_fps * 0.5)
    if ss.keyframes_only and (r.returncode != 0 or len(frames) < max(1, expected)):
        r, frames = run(False)  # codec without usable keyframe skipping: decode everything
    if len(frames) == 0:
        raise ProbeError("FFmpeg returned no frames: " + r.stderr.decode("utf-8", "replace")[-200:])
    return frames, w, h, info.duration_s


# --------------------------------------------------------------------------------------
# Faces
# --------------------------------------------------------------------------------------

class FaceDetector:
    """YuNet via OpenCV; unavailable = None from create()."""

    def __init__(self, det, w: int, h: int):
        self.det, self.w, self.h = det, w, h

    @staticmethod
    def create(w: int, h: int, score: float) -> Optional["FaceDetector"]:
        try:
            import cv2  # noqa: F401
        except Exception:
            return None
        if not os.path.isfile(MODEL_FILE):
            return None
        import cv2
        cv2.setNumThreads(1)  # our worker threads run in parallel already; no nested OpenCV threads
        det = cv2.FaceDetectorYN.create(MODEL_FILE, "", (w, h), score, 0.3, 50)
        return FaceDetector(det, w, h)

    def detect(self, rgb: np.ndarray) -> np.ndarray:
        """(k, 5): x, y, w, h, score in pixels."""
        bgr = np.ascontiguousarray(rgb[..., ::-1])
        _, faces = self.det.detect(bgr)
        if faces is None:
            return np.zeros((0, 5))
        return np.concatenate([faces[:, :4], faces[:, 14:15]], axis=1)


def model_ok() -> bool:
    if not os.path.isfile(MODEL_FILE):
        return False
    with open(MODEL_FILE, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest() == MODEL_SHA256


# --------------------------------------------------------------------------------------
# Per take
# --------------------------------------------------------------------------------------

_Y = np.array([0.2126, 0.7152, 0.0722])


def analyse_frames(frames: np.ndarray, w: int, h: int, ss: ShotSettings, fd: Optional[FaceDetector]) -> list[Sample]:
    out: list[Sample] = []
    prev_small = None
    for i, fr in enumerate(frames):
        rgb = fr.astype(np.float32) / 255.0
        y = luma(rgb)
        p05, p95 = np.percentile(y, [5, 95])
        lap = ndimage.laplace(y)
        sharp = float(np.log10(lap.var() * 255 * 255 + 1e-6))
        small = y[: h - h % 4, : w - w % 4].reshape(h // 4, 4, w // 4, 4).mean(axis=(1, 3))
        motion = float(np.mean(np.abs(small - prev_small))) if prev_small is not None else 0.0
        prev_small = small
        faces, face_h, fx, fy, face_lab = 0, 0.0, 0.5, 0.5, None
        if fd is not None:
            det = fd.detect(fr)
            det = det[det[:, 4] >= ss.face_score] if len(det) else det
            faces = int(len(det))
            if faces:
                j = int(np.argmax(det[:, 3]))
                face_h = float(det[j, 3] / h)
                fx = float((det[j, 0] + det[j, 2] / 2) / w)
                fy = float((det[j, 1] + det[j, 3] / 2) / h)
                # inner 60% of the box: cheeks, nose, forehead (little hair or background)
                x0, y0, bw, bh = det[j, :4]
                xa, xb = int(max(0, x0 + 0.2 * bw)), int(min(w, x0 + 0.8 * bw))
                ya, yb = int(max(0, y0 + 0.2 * bh)), int(min(h, y0 + 0.8 * bh))
                if xb - xa >= 4 and yb - ya >= 4:
                    face_lab = lab_stats(rgb[ya:yb, xa:xb])
        out.append(Sample(t=round(i / ss.sample_fps, 3), luma=round(float(y.mean()), 4), p05=round(float(p05), 4),
                          p95=round(float(p95), 4), clip_lo=round(float(np.mean(y < 0.02)), 4),
                          clip_hi=round(float(np.mean(y > 0.98)), 4), sharp=round(sharp, 4), motion=round(motion, 4),
                          faces=faces, face_h=round(face_h, 4), face_x=round(fx, 4), face_y=round(fy, 4),
                          lab=lab_stats(rgb), face_lab=face_lab))
    return out


def signature(frames: np.ndarray) -> list[float]:
    """Median 32x18 luma thumbnail, zero-mean unit-norm: the camera setup's background."""
    thumbs = []
    for fr in frames[:: max(1, len(frames) // 24)]:
        y = luma(fr.astype(np.float32) / 255.0)
        hh, ww = y.shape
        ys = np.linspace(0, hh, 19).astype(int)
        xs = np.linspace(0, ww, 33).astype(int)
        thumbs.append([[y[ys[r]:ys[r + 1], xs[c]:xs[c + 1]].mean() for c in range(32)] for r in range(18)])
    t = np.median(np.array(thumbs), axis=0).ravel()
    t = t - t.mean()
    n = np.linalg.norm(t)
    return [round(float(x), 5) for x in (t / n if n > 1e-9 else t)]


def take_size(samples: list[Sample], ss: ShotSettings) -> Optional[str]:
    hs = [s.face_h for s in samples if s.faces and s.p95 >= ss.black_luma]
    if not samples:
        return None
    if len(hs) < max(1, len(samples) // 4):
        return "wide"
    m = float(np.median(hs))
    return "close" if m >= ss.close_face else "medium" if m >= ss.wide_face else "wide"


def analyse_take(clip_id: str, path: str, ss: ShotSettings, use_faces: bool = True) -> TakeShots:
    try:
        frames, w, h, dur = sample_frames(path, ss)
    except ProbeError as e:
        return TakeShots(clip_id=clip_id, path=path, width=0, height=0, duration_s=0.0, face_detector="none",
                         samples=[], error=str(e))
    fd = FaceDetector.create(w, h, ss.face_score) if use_faces else None
    samples = analyse_frames(frames, w, h, ss, fd)
    return TakeShots(clip_id=clip_id, path=path, width=w, height=h, duration_s=dur,
                     face_detector="yunet" if fd else "none", samples=samples, size=take_size(samples, ss),
                     signature=signature(frames), colour=pool_stats([s.lab for s in samples if s.p95 >= ss.black_luma]),
                     face_colour=pool_stats([s.face_lab for s in samples if s.face_lab is not None]))


# --------------------------------------------------------------------------------------
# Setups, quality, all takes
# --------------------------------------------------------------------------------------

def group_setups(takes: dict[str, TakeShots], ss: ShotSettings) -> dict[str, str]:
    """Average-linkage clustering of setup signatures; distance = 1 - correlation."""
    ids = [k for k in sorted(takes) if takes[k].signature]
    if not ids:
        return {}
    sig = np.array([takes[k].signature for k in ids])
    dist = 1.0 - np.clip(sig @ sig.T, -1, 1)
    clusters = [[i] for i in range(len(ids))]
    while len(clusters) > 1:
        best, pair = None, None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                d = float(np.mean([dist[i, j] for i in clusters[a] for j in clusters[b]]))
                if best is None or d < best:
                    best, pair = d, (a, b)
        if best is None or best >= ss.setup_distance:
            break
        a, b = pair
        clusters[a] = clusters[a] + clusters[b]
        del clusters[b]
    clusters.sort(key=lambda c: min(c))
    out = {}
    for n, c in enumerate(clusters, start=1):
        for i in c:
            out[ids[i]] = f"S{n}"
    return out


def sample_quality(s: Sample, sharp_ref: tuple[float, float], ss: ShotSettings) -> float:
    """0..1: exposure (mid luma, little clipping), relative sharpness, black frames = 0."""
    if s.p95 < ss.black_luma:
        return 0.0
    expo = 1.0 - min(1.0, abs(s.luma - 0.45) / 0.35)
    expo *= 1.0 - min(1.0, 4.0 * max(0.0, s.clip_hi - 0.02) + 4.0 * max(0.0, s.clip_lo - 0.05))
    mu, sd = sharp_ref
    sharp = 1.0 / (1.0 + math.exp(-(s.sharp - mu) / max(sd, 1e-3)))
    return round(0.5 * expo + 0.5 * sharp, 4)


def shots_cache_file(cache_dir: str, clip_id: str, ss: ShotSettings, faces: bool) -> str:
    return os.path.join(cache_dir, "shots", f"{clip_id}_{ss.key()}_r{SHOTS_REVISION}{'f' if faces else 'n'}.json")


def analyse_takes(clips: list[tuple[str, str]], ss: Optional[ShotSettings] = None, cache_dir: Optional[str] = None,
                  workers: int = 4, progress: Optional[Callable[[int, int, str], None]] = None,
                  use_faces: bool = True) -> ShotsReport:
    """clips = [(clip_id, path)]. Cached per clip id + settings."""
    ss = ss or ShotSettings()
    takes: dict[str, TakeShots] = {}
    todo = []
    for cid, path in clips:
        cf = shots_cache_file(cache_dir, cid, ss, use_faces) if cache_dir else None
        if cf and os.path.isfile(cf):
            try:
                with open(cf, encoding="utf-8") as f:
                    takes[cid] = TakeShots.model_validate(json.load(f))
                continue
            except Exception:
                pass
        todo.append((cid, path, cf))
    done = [len(takes)]

    def work(item):
        cid, path, cf = item
        t = analyse_take(cid, path, ss, use_faces)
        if cf and t.error is None:
            os.makedirs(os.path.dirname(cf), exist_ok=True)
            tmp = cf + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(t.model_dump_json())
            os.replace(tmp, cf)
        return cid, t

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for cid, t in ex.map(work, todo):
            takes[cid] = t
            done[0] += 1
            if progress:
                progress(done[0], len(clips), t.path)
    return ShotsReport(settings=ss, takes=takes, setups=group_setups(takes, ss))


def shot_info_from_report(rep: ShotsReport):
    """engine.edit.ShotInfo per clip, with per-sample quality and size for window lookups."""
    from .edit import ShotInfo
    all_sharp = [s.sharp for t in rep.takes.values() for s in t.samples if s.p95 >= rep.settings.black_luma]
    ref = (float(np.mean(all_sharp)), float(np.std(all_sharp))) if all_sharp else (0.0, 1.0)
    out = {}
    for cid, t in rep.takes.items():
        if not t.samples:
            continue
        q = [sample_quality(s, ref, rep.settings) for s in t.samples]
        sizes = []
        for s in t.samples:
            if not s.faces:
                sizes.append("wide" if t.face_detector == "yunet" else None)
            else:
                sizes.append("close" if s.face_h >= rep.settings.close_face else "medium" if s.face_h >= rep.settings.wide_face else "wide")
        faces = [s.faces > 0 for s in t.samples]
        out[cid] = ShotInfo(quality=float(np.mean(q)), size=t.size, setup=rep.setups.get(cid),
                            times=[s.t for s in t.samples], qualities=q, sizes=sizes, faces=faces)
    return out
