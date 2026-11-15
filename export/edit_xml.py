"""FCP7 XML (xmeml 5) for an edit plan: the first cut as a Premiere sequence.

V1 = the cut (performance and story shots; gaps stay empty), A1 = the song, sequence markers per
section, optional effects. Same unit rules as export/fcp7_xml.py: start/end/in/out in sequence
frames, end/out exclusive.

Evidence for what Premiere keeps on import (docs/EDIT_DESIGN.md section 2):
  * cuts, sequence markers, basic motion and opacity incl. keyframes: VERIFIED (Adobe's "Importing
    XML project files from Final Cut Pro" page, archived 2026-01-23);
  * transitions: "Dip To White -> Dip To White", "Other video transitions -> Cross Dissolve": VERIFIED;
    `transitionitem` shape from Apple's xmeml reference.
  * Keyframe `when` is written in the clip's media frames (in-point based), the convention Final Cut
    Pro 7 exports use; Premiere's reading of it is NOT VERIFIED and is checked by the self-test.
"""

from __future__ import annotations

import os
from fractions import Fraction
from typing import Optional
from xml.sax.saxutils import escape

from engine.edit import EditPlan, Shot
from engine.result import SyncResult
from export.fcp7_xml import _file_xml, _frames, _rate, pathurl

_COLOURS = {"red", "orange", "yellow", "green", "blue", "cyan", "magenta", "purple"}


def _marker(name: str, comment: str, start: int, frames: int, colour: Optional[str]) -> str:
    col = f"<color>{escape(colour)}</color>" if colour in _COLOURS else ""
    return (f"<marker><name>{escape(name)}</name><comment>{escape(comment)}</comment>"
            f"<in>{start}</in><out>{start + max(0, frames)}</out>{col}</marker>")


def _motion_filter(shot: Shot, rate: str) -> str:
    """Scale keyframes for a punch-in: 100% at the shot start, `amount` after `frames`."""
    for e in shot.effects:
        if e.kind != "punch_in":
            continue
        k0, k1 = shot.src_in, shot.src_in + min(e.frames, shot.end - shot.start - 1)
        return ("<filter><effect><name>Basic Motion</name><effectid>basic</effectid>"
                "<effectcategory>motion</effectcategory><effecttype>motion</effecttype><mediatype>video</mediatype>"
                "<parameter authoringApp=\"PremierePro\"><parameterid>scale</parameterid><name>Scale</name>"
                "<valuemin>0</valuemin><valuemax>1000</valuemax><value>100</value>"
                f"<keyframe><when>{k0}</when><value>100</value></keyframe>"
                f"<keyframe><when>{k1}</when><value>{e.amount:g}</value></keyframe>"
                "</parameter></effect></filter>")
    return ""


def _transition(name: str, cut: int, frames: int, rate: str) -> str:
    half = max(1, frames // 2)
    return (f"<transitionitem>{rate}<start>{cut - half}</start><end>{cut + (frames - half)}</end>"
            "<alignment>center</alignment>"
            f"<effect><name>{escape(name)}</name><effectid>{escape(name)}</effectid>"
            "<effectcategory>Dissolve</effectcategory><effecttype>transition</effecttype>"
            "<mediatype>video</mediatype></effect></transitionitem>")


def build_edit_xml(result: SyncResult, plan: EditPlan, sequence_name: str, luts: Optional[dict[str, str]] = None) -> str:
    """`luts`: clip_id -> .cube path, noted in each clip's comment (Premiere's API cannot apply a LUT;
    the editor loads it in Lumetri > Basic Correction > Input LUT)."""
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
            f"<duration>{total}</duration>{rate}<start>{s.start}</start><end>{s.end}</end>"
            f"<in>{s.src_in}</in><out>{s.src_out}</out>{file_xml}{_motion_filter(s, rate)}"
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
        f"{rate}<width>{result.sequence.width}</width><height>{result.sequence.height}</height>"
        "<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>"
        f"{video}</video><audio>{audio}</audio></media></sequence></xmeml>\n")


def write_edit_xml(result: SyncResult, plan: EditPlan, out_path: str, sequence_name: str,
                   luts: Optional[dict[str, str]] = None) -> dict:
    text = build_edit_xml(result, plan, sequence_name, luts)
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
