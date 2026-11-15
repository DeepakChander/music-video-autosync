"""Social versions of a cut: 9:16, 1:1 and 4:5 sequences that follow the subject.

For each shot the horizontal centre of the crop is the median face position (engine/shots.py
samples) over the shot's source window, or the picture centre when no face was found. The crop
covers the frame (no black bars): scale = max(out_w / src_w, out_h / src_h).
"""

from __future__ import annotations

import statistics
from typing import Optional

ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}


def centre_for(shots_report, clip_id: Optional[str], a_s: float, b_s: float) -> tuple[float, float]:
    """(x, y) in 0..1 of the source picture to keep centred for clip time [a_s, b_s)."""
    if shots_report is None or clip_id is None or clip_id not in shots_report.takes:
        return 0.5, 0.5
    t = shots_report.takes[clip_id]
    xs = [(s.face_x, s.face_y) for s in t.samples if s.faces and a_s - 0.25 <= s.t < b_s + 0.25]
    if not xs:
        xs = [(s.face_x, s.face_y) for s in t.samples if s.faces]
        if not xs:
            return 0.5, 0.5
        # a take whose subject sits on one side keeps that side even where no face was found
    return round(statistics.median(x for x, _ in xs), 4), round(statistics.median(y for _, y in xs), 4)


def crop_window(src_w: int, src_h: int, out_w: int, out_h: int, cx: float, cy: float) -> tuple[float, float, float]:
    """(scale, crop_x, crop_y): the source is scaled by `scale` and the out_w x out_h window starts
    at (crop_x, crop_y) in scaled pixels, clamped inside the picture."""
    s = max(out_w / src_w, out_h / src_h)
    sw, sh = src_w * s, src_h * s
    x = min(max(cx * sw - out_w / 2, 0.0), sw - out_w)
    y = min(max(cy * sh - out_h / 2, 0.0), sh - out_h)
    return s, x, y


def reframe_vf(out_w: int, out_h: int, cx: float, cy: float) -> str:
    """FFmpeg filter: cover-scale, then crop around (cx, cy) of the source, clamped."""
    return (f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase:in_color_matrix=bt709:out_color_matrix=bt709,"
            f"crop={out_w}:{out_h}:x='max(0,min(iw-{out_w},iw*{cx:.4f}-{out_w}/2))':y='max(0,min(ih-{out_h},ih*{cy:.4f}-{out_h}/2))',"
            "setsar=1")


def shot_centres(plan, fps, shots_report) -> list[tuple[float, float]]:
    """Crop centre per shot of an EditPlan or AdPlan (gaps / missing shots: picture centre)."""
    out = []
    for sh in plan.shots:
        cid = getattr(sh, "clip_id", None)
        if cid is None or getattr(sh, "kind", "") == "gap" or getattr(sh, "status", "") == "missing":
            out.append((0.5, 0.5))
            continue
        out.append(centre_for(shots_report, cid, sh.src_in / float(fps), sh.src_out / float(fps)))
    return out
