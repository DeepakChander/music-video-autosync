"""xmeml filters shared by the cut writers: constant speed (Time Remap) and Basic Motion
(scale, centre, scale keyframes). Evidence: docs/DECISIONS.md D-028.

Time Remap (Apple xmeml reference + three real exports, Premiere and FCP7): a clipitem's <in>/<out>
are in *retimed* frames (out - in = end - start), <duration> = media length * 100 / speed, and the
source frames appear only in the graphdict keyframes (when = retimed frame, value = source frame,
both in the sequence timebase).

Basic Motion (Apple reference + four real Premiere exports): scale in percent of the clip's native
size; center horiz/vert as a fraction of the clip's own (unscaled) frame, 0 = centred, negative =
left/up. To centre source point fx after scaling by s: horiz = -(fx - 0.5) * s.
"""

from __future__ import annotations

from typing import Optional


def timeremap_filter(speed: float, in_r: int, out_r: int, src_in: float, src_out: float, duration_r: int, media: int) -> str:
    """speed in 0..1 (0.5 = 50%); in_r/out_r/duration_r in retimed frames; src_* and media in source
    frames (sequence timebase)."""
    pct = round(speed * 100, 4)
    kfs = []
    if in_r > 0:
        kfs.append("<keyframe><when>0</when><value>0</value><speedvirtualkf>TRUE</speedvirtualkf><speedkfstart>TRUE</speedkfstart></keyframe>")
    kfs.append(f"<keyframe><when>{in_r}</when><value>{src_in:g}</value><speedvirtualkf>TRUE</speedvirtualkf><speedkfin>TRUE</speedkfin></keyframe>")
    kfs.append(f"<keyframe><when>{out_r}</when><value>{src_out:g}</value><speedvirtualkf>TRUE</speedvirtualkf><speedkfout>TRUE</speedkfout></keyframe>")
    kfs.append(f"<keyframe><when>{duration_r}</when><value>{media}</value><speedvirtualkf>TRUE</speedvirtualkf><speedkfend>TRUE</speedkfend></keyframe>")
    return ("<filter><effect><name>Time Remap</name><effectid>timeremap</effectid><effectcategory>motion</effectcategory>"
            "<effecttype>motion</effecttype><mediatype>video</mediatype>"
            "<parameter><parameterid>variablespeed</parameterid><name>variablespeed</name><valuemin>0</valuemin><valuemax>1</valuemax><value>0</value></parameter>"
            f"<parameter><parameterid>speed</parameterid><name>speed</name><valuemin>-100000</valuemin><valuemax>100000</valuemax><value>{pct:g}</value></parameter>"
            "<parameter><parameterid>reverse</parameterid><name>reverse</name><value>FALSE</value></parameter>"
            "<parameter><parameterid>frameblending</parameterid><name>frameblending</name><value>FALSE</value></parameter>"
            f"<parameter><parameterid>graphdict</parameterid><name>graphdict</name><valuemin>0</valuemin><valuemax>{media}</valuemax><value>0</value>"
            + "".join(kfs) + "<interpolation><name>FCPCurve</name></interpolation></parameter></effect></filter>")


def reframe_motion(src_w: int, src_h: int, out_w: int, out_h: int, cx: float, cy: float) -> tuple[float, float, float]:
    """(scale_pct, horiz, vert) that fill out_w x out_h with the source and centre source point
    (cx, cy), clamped so no black edge shows."""
    s = max(out_w / src_w, out_h / src_h)
    horiz = -(cx - 0.5) * s
    vert = -(cy - 0.5) * s
    hmax = max(0.0, (src_w * s - out_w) / (2 * src_w))
    vmax = max(0.0, (src_h * s - out_h) / (2 * src_h))
    horiz = min(max(horiz, -hmax), hmax)
    vert = min(max(vert, -vmax), vmax)
    return round(s * 100, 4), round(horiz, 6), round(vert, 6)


def motion_filter(scale_pct: float = 100.0, center: Optional[tuple[float, float]] = None,
                  scale_keys: Optional[list[tuple[int, float]]] = None) -> str:
    """Basic Motion: a fixed scale or scale keyframes [(when, percent)], and an optional centre."""
    if scale_keys:
        scale = ("<parameter authoringApp=\"PremierePro\"><parameterid>scale</parameterid><name>Scale</name>"
                 f"<valuemin>0</valuemin><valuemax>1000</valuemax><value>{scale_keys[0][1]:g}</value>"
                 + "".join(f"<keyframe><when>{w}</when><value>{v:g}</value></keyframe>" for w, v in scale_keys) + "</parameter>")
    else:
        scale = ("<parameter authoringApp=\"PremierePro\"><parameterid>scale</parameterid><name>Scale</name>"
                 f"<valuemin>0</valuemin><valuemax>1000</valuemax><value>{scale_pct:g}</value></parameter>")
    ctr = ""
    if center is not None:
        ctr = ("<parameter authoringApp=\"PremierePro\"><parameterid>center</parameterid><name>Center</name>"
               f"<value><horiz>{center[0]:g}</horiz><vert>{center[1]:g}</vert></value></parameter>")
    return ("<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>"
            "<effecttype>motion</effecttype><mediatype>video</mediatype>" + scale + ctr + "</effect></filter>")
