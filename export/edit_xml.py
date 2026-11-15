"""FCP7 XML (xmeml 5) for an edit plan: the first cut as a Premiere sequence.

V1 = the cut (performance and story shots; gaps stay empty), A1 = the song, sequence markers per
section, optional effects. Same unit rules as export/fcp7_xml.py: start/end/in/out in sequence
frames, end/out exclusive.

Evidence for what Premiere keeps on import (docs/EDIT_DESIGN.md section 2):
  * cuts, sequence markers, basic motion and opacity incl. keyframes: VERIFIED (Adobe's "Importing
    XML project files from Final Cut Pro" page, archived 2026-01-23);
  * transitions: "Dip To White -> Dip To White", "Other video transitions -> Cross Dissolve": VERIFIED;
    `transitionitem` shape from Apple's xmeml reference.
  * Keyframe `when` is written in the clip's frames counted like <in> (the convention of FCP7 and
    Premiere exports); constant speed as Time Remap and social-version framing as Basic Motion
    (export/xml_motion.py, docs/DECISIONS.md D-028). Premiere's reading of these is NOT VERIFIED on a
    real import yet.
"""

from __future__ import annotations

import os
from fractions import Fraction
from typing import Optional
from xml.sax.saxutils import escape

from engine.edit import EditPlan, Shot
from engine.result import SyncResult
from export.fcp7_xml import _file_xml, _frames, _rate, pathurl
from export.xml_motion import motion_filter, reframe_motion, timeremap_filter

_COLOURS = {"red", "orange", "yellow", "green", "blue", "cyan", "magenta", "purple"}


def _marker(name: str, comment: str, start: int, frames: int, colour: Optional[str]) -> str:
    col = f"<color>{escape(colour)}</color>" if colour in _COLOURS else ""
    return (f"<marker><name>{escape(name)}</name><comment>{escape(comment)}</comment>"
            f"<in>{start}</in><out>{start + max(0, frames)}</out>{col}</marker>")


def _motion_filter(shot: Shot, in_r: int, base_scale: float = 100.0, center: Optional[tuple[float, float]] = None) -> str:
    """Basic Motion: punch-in scale keyframes (from the framing scale to amount x it, keyframe times
    in the clip's frames from its in point) and/or the framing of a social version."""
    for e in shot.effects:
        if e.kind != "punch_in":
            continue
        k0, k1 = in_r, in_r + min(e.frames, shot.end - shot.start - 1)
        return motion_filter(scale_keys=[(k0, base_scale), (k1, round(base_scale * e.amount / 100.0, 4))], center=center)
    if center is not None or abs(base_scale - 100.0) > 1e-9:
        return motion_filter(scale_pct=base_scale, center=center)
    return ""


def _transition(name: str, cut: int, frames: int, rate: str) -> str:
    half = max(1, frames // 2)
    return (f"<transitionitem>{rate}<start>{cut - half}</start><end>{cut + (frames - half)}</end>"
            "<alignment>center</alignment>"
            f"<effect><name>{escape(name)}</name><effectid>{escape(name)}</effectid>"
            "<effectcategory>Dissolve</effectcategory><effecttype>transition</effecttype>"
            "<mediatype>video</mediatype></effect></transitionitem>")


def build_edit_xml(result: SyncResult, plan: EditPlan, sequence_name: str, luts: Optional[dict[str, str]] = None,
                   frame: Optional[dict] = None) -> str:
    """`luts`: clip_id -> .cube path, noted in each clip's comment (Premiere's API cannot apply a LUT;
    the editor loads it in Lumetri > Basic Correction > Input LUT).
    `frame`: a social version {width, height, centres: [(cx, cy) per shot], sources: {clip_id: (w, h)}}:
    the sequence takes that size and every shot is scaled to fill it around its centre point."""
    luts = luts or {}
    fps = Fraction(plan.fps_num, plan.fps_den)
    rate = _rate(fps)
    clips = {c.clip_id: c for c in result.clips}
    defined: set[str] = set()
    items = []
    for i, s in enumerate(plan.shots):
        if s.kind == "gap" or not s.clip_id:
            continue
        c = clips[s.clip_id]
        fid = f"file-{c.clip_id}"
        if fid in defined:
            file_xml = f'<file id="{fid}"/>'
        else:
            file_xml = _file_xml(c, fid, fps)
            defined.add(fid)
        total = max(1, _frames(c.duration_s, fps))
        n = s.end - s.start
        speed = getattr(s, "speed", 1.0) or 1.0
        if abs(speed - 1.0) > 1e-9:  # retimed frames: in/out/duration in the clip's slowed frames
            in_r = int(round(s.src_in / speed))
            out_r = in_r + n
            dur_r = int(round(total / speed))
            remap = timeremap_filter(speed, in_r, out_r, s.src_in, s.src_in + n * speed, dur_r, total)
        else:
            in_r, out_r, dur_r, remap = s.src_in, s.src_out, total, ""
        base, ctr = 100.0, None
        if frame is not None:
            src = (frame.get("sources") or {}).get(s.clip_id)
            if src:
                cx, cy = frame["centres"][i] if frame.get("centres") else (0.5, 0.5)
                base, hz, vt = reframe_motion(src[0], src[1], frame["width"], frame["height"], cx, cy)
                ctr = (hz, vt)
        # Transitions sit on the cut before this shot.
        prev = plan.shots[i - 1] if i > 0 else None
        if prev is not None and prev.kind != "gap":
            for e in s.effects:
                if e.kind == "flash":
                    items.append(_transition("Dip to White", s.start, e.frames, rate))
                elif e.kind == "dissolve":
                    items.append(_transition("Cross Dissolve", s.start, e.frames, rate))
        items.append(
            f'<clipitem id="cut-{i}"><name>{escape(c.name)}</name><enabled>TRUE</enabled>'
            f"<duration>{dur_r}</duration>{rate}<start>{s.start}</start><end>{s.end}</end>"
            f"<in>{in_r}</in><out>{out_r}</out>{file_xml}{remap}{_motion_filter(s, in_r, base, ctr)}"
            f"<comments><mastercomment1>{escape(s.reason)}</mastercomment1>"
            + (f"<mastercomment2>Input LUT: {escape(luts[s.clip_id])}</mastercomment2>" if s.clip_id in luts else "")
            + "</comments></clipitem>")
    video = f"<track>{''.join(items)}<enabled>TRUE</enabled><locked>FALSE</locked></track>"

    master_frames = max(1, _frames(result.master.duration_s, fps))
    master_name = os.path.basename(result.master.path.replace("\\", "/"))
    master_item = (
        f'<clipitem id="master"><name>{escape(os.path.splitext(master_name)[0])}</name><enabled>TRUE</enabled>'
        f"<duration>{master_frames}</duration>{rate}<start>0</start><end>{master_frames}</end>"
        f"<in>0</in><out>{master_frames}</out>"
        f'<file id="file-master"><name>{escape(master_name)}</name><pathurl>{escape(pathurl(result.master.path))}</pathurl>'
        f"{rate}<duration>{master_frames}</duration><media><audio><channelcount>2</channelcount></audio></media></file>"
        "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>")
    audio = f'<track premiereTrackType="Stereo">{master_item}<enabled>TRUE</enabled><locked>FALSE</locked></track>'
    markers = "".join(_marker(m.name, m.comment, m.start, m.frames, m.colour) for m in plan.markers)
    duration = max(plan.duration_frames, master_frames)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="5">'
        f'<sequence id="sequence-cut"><name>{escape(sequence_name)}</name><duration>{duration}</duration>{rate}'
        f"<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
        f"{markers}"
        "<media><video><format><samplecharacteristics>"
        f"{rate}<width>{frame['width'] if frame else result.sequence.width}</width>"
        f"<height>{frame['height'] if frame else result.sequence.height}</height>"
        "<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>"
        f"{video}</video><audio>{audio}</audio></media></sequence></xmeml>\n")


def write_edit_xml(result: SyncResult, plan: EditPlan, out_path: str, sequence_name: str,
                   luts: Optional[dict[str, str]] = None, frame: Optional[dict] = None) -> dict:
    text = build_edit_xml(result, plan, sequence_name, luts, frame)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, out_path)
    return {"path": os.path.abspath(out_path), "shots": sum(1 for s in plan.shots if s.kind != "gap"),
            "markers": len(plan.markers)}


def cut_sequence_name(song_path: str, style: str, when: Optional[str] = None) -> str:
    import datetime as _dt
    song = os.path.splitext(os.path.basename(song_path.replace("\\", "/")))[0]
    ts = when or _dt.datetime.now().strftime("%Y-%m-%d %H%M%S")
    return f"AutoSync Cut - {song} - {style} - {ts}"
