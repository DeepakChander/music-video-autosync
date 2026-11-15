"""What the panel needs to build a cut directly through Premiere's UXP API (the route used when
Premiere does not create a sequence from the imported XML). Times are Premiere ticks (strings).

Track layout:
  music video cut: V1 shots (camera sound lands on A2 and is muted), A1 the song.
  ad cut: V1 shots with their sound on A1 (picture-only shots' sound disabled), A2 VO, A3 music.
"""

from __future__ import annotations

import os
from fractions import Fraction
from typing import Optional

from engine.ad import AdPlan, Clip
from engine.edit import EditPlan
from engine.result import SyncResult
from engine.timebase import ticks_per_frame


def _t(frames: int, tpf: Fraction) -> str:
    v = frames * tpf
    if v.denominator != 1:
        raise ValueError("frame rate without whole ticks per frame")
    return str(v.numerator)


def cut_payload(result: SyncResult, plan: EditPlan, sequence_name: str) -> dict:
    fps = Fraction(plan.fps_num, plan.fps_den)
    tpf = ticks_per_frame(fps)
    paths = {c.clip_id: c.path for c in result.clips}
    video = []
    skipped = []
    for s in plan.shots:
        if s.kind == "gap" or not s.clip_id:
            continue
        if abs(getattr(s, "speed", 1.0) - 1.0) > 1e-9:
            # UXP has no speed setter (getSpeed is read-only): a slow-motion shot placed at 100%
            # would be out of sync, so it is left out of this route and reported.
            skipped.append(f"{os.path.basename(paths[s.clip_id])} at {s.start}")
            continue
        video.append({"path": paths[s.clip_id], "name": os.path.basename(paths[s.clip_id]), "startTicks": _t(s.start, tpf),
                      "inTicks": _t(s.src_in, tpf), "outTicks": _t(s.src_out, tpf), "audioTrack": 1, "keepAudio": False})
    master_frames = plan.duration_frames
    audio = [{"path": result.master.path, "name": os.path.basename(result.master.path), "startTicks": "0", "inTicks": "0",
              "outTicks": _t(master_frames, tpf), "audioTrack": 0}]
    transitions = []
    for i, s in enumerate(plan.shots):
        if i == 0 or plan.shots[i - 1].kind == "gap" or s.kind == "gap":
            continue
        for e in s.effects:
            if e.kind in ("flash", "dissolve"):
                transitions.append({"atTicks": _t(s.start, tpf), "frames": e.frames,
                                    "kind": "dip_white" if e.kind == "flash" else "dissolve"})
    return {"kind": "music", "sequenceName": sequence_name, "fpsNum": plan.fps_num, "fpsDen": plan.fps_den,
            "ticksPerFrame": str(int(tpf)), "width": result.sequence.width, "height": result.sequence.height,
            "video": video, "audio": audio, "muteAudioTracks": [1],
            "markers": [{"startTicks": _t(m.start, tpf), "durationTicks": _t(m.frames, tpf), "name": m.name,
                         "comment": m.comment, "colour": m.colour} for m in plan.markers],
            "transitions": transitions,
            "notes": ([f"{len(skipped)} slow-motion shot(s) left empty: Premiere's API cannot set clip speed. "
                       "Import the XML (File > Import) to get them."] if skipped else [])}


def ad_payload(plan: AdPlan, clips: list[Clip], sequence_name: str, width: int, height: int,
               vo_s: Optional[float] = None, music_s: Optional[float] = None) -> dict:
    fps = Fraction(plan.fps_num, plan.fps_den)
    tpf = ticks_per_frame(fps)
    by_id = {c.clip_id: c for c in clips}
    video = []
    for a in plan.shots:
        if a.status == "missing" or not a.clip_id:
            continue
        c = by_id[a.clip_id]
        video.append({"path": c.path, "name": c.name, "startTicks": _t(a.start, tpf), "inTicks": _t(a.src_in, tpf),
                      "outTicks": _t(a.src_out, tpf), "audioTrack": 0,
                      "keepAudio": a.kind == "dialogue" and a.line_score is not None})
    audio = []
    if plan.vo_path and vo_s:
        vo_total = int(Fraction(vo_s).limit_denominator(10**6) * fps)
        for a in plan.shots:
            if a.vo_in_s is None or a.vo_out_s is None:
                continue
            vin = int(round(a.vo_in_s * float(fps)))
            n = min(int(round(a.vo_out_s * float(fps))) - vin, a.end - a.start, max(0, vo_total - vin))
            if n > 0:
                audio.append({"path": plan.vo_path, "name": f"VO {a.shot_id}", "startTicks": _t(a.start, tpf),
                              "inTicks": _t(vin, tpf), "outTicks": _t(vin + n, tpf), "audioTrack": 1})
    if plan.music_path and music_s:
        n = min(int(Fraction(music_s).limit_denominator(10**6) * fps), plan.duration_frames)
        audio.append({"path": plan.music_path, "name": os.path.basename(plan.music_path), "startTicks": "0", "inTicks": "0",
                      "outTicks": _t(n, tpf), "audioTrack": 2})
    colour = {"missing": "red", "suggested": "yellow"}
    markers = []
    for a in plan.shots:
        name = f"MISSING {a.shot_id}" if a.status == "missing" else (f"CHECK {a.shot_id}" if a.status == "suggested" else a.shot_id)
        comment = " | ".join(x for x in (a.description, f'"{a.dialogue}"' if a.dialogue else "", a.reason,
                                          f"SUPER: {a.super_text}" if a.super_text else "") if x)
        c = colour.get(a.status) or ("yellow" if a.confidence == "low" else "green" if a.confidence == "high" else "blue")
        markers.append({"startTicks": _t(a.start, tpf), "durationTicks": _t(a.end - a.start, tpf), "name": name,
                        "comment": comment, "colour": c})
    return {"kind": "ad", "sequenceName": sequence_name, "fpsNum": plan.fps_num, "fpsDen": plan.fps_den,
            "ticksPerFrame": str(int(tpf)), "width": width, "height": height, "video": video, "audio": audio,
            "muteAudioTracks": [], "markers": markers, "transitions": []}
