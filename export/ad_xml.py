"""FCP7 XML (xmeml 5) for an ad first cut (engine/ad.py).

V1 picture in script order; A1 the dialogue takes' own sound (linked to their picture); A2 the VO
recording cut per line; A3 the music bed; one sequence marker per shot (red "MISSING", yellow
for storyboard suggestions and low confidence). Units as in export/fcp7_xml.py: sequence frames,
end/out exclusive. Missing shots stay empty: nothing is filled with a guess.
"""

from __future__ import annotations

import os
from fractions import Fraction
from typing import Optional
from xml.sax.saxutils import escape

from engine.ad import AdPlan, Clip
from export.fcp7_xml import _frames, _rate, pathurl
from export.xml_motion import motion_filter, reframe_motion


def _file(fid: str, path: str, frames: int, rate: str, video: bool, audio: bool) -> str:
    media = "<media>" + ("<video/>" if video else "") + ("<audio><channelcount>2</channelcount></audio>" if audio else "") + "</media>"
    return (f'<file id="{fid}"><name>{escape(os.path.basename(path.replace(chr(92), "/")))}</name>'
            f"<pathurl>{escape(pathurl(path))}</pathurl>{rate}<duration>{frames}</duration>{media}</file>")


def _colour(a) -> str:
    if a.status == "missing":
        return "red"
    if a.status == "suggested" or a.confidence == "low":
        return "yellow"
    return "green" if a.confidence == "high" else "blue"


def build_ad_xml(plan: AdPlan, clips: list[Clip], sequence_name: str, width: int, height: int,
                 music_s: Optional[float] = None, vo_s: Optional[float] = None,
                 luts: Optional[dict[str, str]] = None, frame: Optional[dict] = None) -> str:
    """`frame`: a social version {width, height, centres: [(cx, cy) per shot], sources: {clip_id: (w, h)}}."""
    if frame is not None:
        width, height = frame["width"], frame["height"]
    fps = Fraction(plan.fps_num, plan.fps_den)
    rate = _rate(fps)
    by_id = {c.clip_id: c for c in clips}
    luts = luts or {}
    defined: set[str] = set()

    def fileref(fid, path, frames, video, audio):
        if fid in defined:
            return f'<file id="{fid}"/>'
        defined.add(fid)
        return _file(fid, path, frames, rate, video, audio)

    v_items, a_items = [], []
    v_index = 0
    a_index = 0
    for i, a in enumerate(plan.shots):
        if a.status == "missing" or not a.clip_id:
            continue
        c = by_id[a.clip_id]
        total = max(1, _frames(c.duration_s, fps))
        fid = f"file-{c.clip_id}"
        v_index += 1
        sound = a.kind == "dialogue" and a.line_score is not None and c.has_audio
        link = ""
        if sound:
            a_index += 1
            link = (f"<link><linkclipref>v-{i}</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex>"
                    f"<clipindex>{v_index}</clipindex></link><link><linkclipref>a-{i}</linkclipref><mediatype>audio</mediatype>"
                    f"<trackindex>1</trackindex><clipindex>{a_index}</clipindex><groupindex>1</groupindex></link>")
        motion = ""
        if frame is not None and (frame.get("sources") or {}).get(c.clip_id):
            sw, sh = frame["sources"][c.clip_id]
            cx, cy = frame["centres"][i] if frame.get("centres") else (0.5, 0.5)
            sc, hz, vt = reframe_motion(sw, sh, width, height, cx, cy)
            motion = motion_filter(scale_pct=sc, center=(hz, vt))
        comment = f"{a.shot_id}: {a.method} ({a.confidence}) {a.reason}"
        lut = f"<mastercomment2>Input LUT: {escape(luts[c.clip_id])}</mastercomment2>" if c.clip_id in luts else ""
        v_items.append(
            f'<clipitem id="v-{i}"><name>{escape(c.name)}</name><enabled>TRUE</enabled><duration>{total}</duration>{rate}'
            f"<start>{a.start}</start><end>{a.end}</end><in>{a.src_in}</in><out>{a.src_out}</out>"
            f"{fileref(fid, c.path, total, c.has_video, c.has_audio)}{motion}{link}"
            f"<comments><mastercomment1>{escape(comment)}</mastercomment1>{lut}</comments></clipitem>")
        if sound:
            a_items.append(
                f'<clipitem id="a-{i}"><name>{escape(c.name)}</name><enabled>TRUE</enabled><duration>{total}</duration>{rate}'
                f"<start>{a.start}</start><end>{a.end}</end><in>{a.src_in}</in><out>{a.src_out}</out>"
                f'{fileref(fid, c.path, total, c.has_video, c.has_audio)}'
                f"<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>{link}</clipitem>")

    vo_items = []
    if plan.vo_path and vo_s:
        vo_total = max(1, _frames(vo_s, fps))
        for i, a in enumerate(plan.shots):
            if a.vo_in_s is None or a.vo_out_s is None:
                continue
            vin = _frames(a.vo_in_s, fps)
            vout = min(vo_total, _frames(a.vo_out_s, fps))
            n = min(vout - vin, a.end - a.start)
            if n <= 0:
                continue
            vo_items.append(
                f'<clipitem id="vo-{i}"><name>VO {escape(a.shot_id)}</name><enabled>TRUE</enabled><duration>{vo_total}</duration>{rate}'
                f"<start>{a.start}</start><end>{a.start + n}</end><in>{vin}</in><out>{vin + n}</out>"
                f"{fileref('file-vo', plan.vo_path, vo_total, False, True)}"
                "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>")

    music_items = []
    if plan.music_path and music_s:
        m_total = max(1, _frames(music_s, fps))
        n = min(m_total, plan.duration_frames)
        music_items.append(
            f'<clipitem id="music"><name>{escape(os.path.splitext(os.path.basename(plan.music_path))[0])}</name>'
            f"<enabled>TRUE</enabled><duration>{m_total}</duration>{rate}<start>0</start><end>{n}</end><in>0</in><out>{n}</out>"
            f"{fileref('file-music', plan.music_path, m_total, False, True)}"
            "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>")

    def track(items, audio=False):
        attr = ' premiereTrackType="Stereo"' if audio else ""
        return f"<track{attr}>{''.join(items)}<enabled>TRUE</enabled><locked>FALSE</locked></track>"

    markers = []
    for a in plan.shots:
        name = f"MISSING {a.shot_id}" if a.status == "missing" else (f"CHECK {a.shot_id}" if a.status == "suggested" else a.shot_id)
        bits = [a.description, (f"\"{a.dialogue}\"" if a.dialogue else ""), a.reason, (f"SUPER: {a.super_text}" if a.super_text else "")]
        markers.append(f"<marker><name>{escape(name)}</name><comment>{escape(' | '.join(b for b in bits if b))}</comment>"
                       f"<in>{a.start}</in><out>{a.end}</out><color>{_colour(a)}</color></marker>")
    audio_tracks = track(a_items, True) + track(vo_items, True) + track(music_items, True)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="5">'
        f'<sequence id="sequence-ad"><name>{escape(sequence_name)}</name><duration>{plan.duration_frames}</duration>{rate}'
        f"<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
        + "".join(markers) +
        "<media><video><format><samplecharacteristics>"
        f"{rate}<width>{width}</width><height>{height}</height><pixelaspectratio>square</pixelaspectratio>"
        "</samplecharacteristics></format>" + track(v_items) + "</video>"
        f"<audio>{audio_tracks}</audio></media></sequence></xmeml>\n")


def write_ad_xml(plan: AdPlan, clips: list[Clip], out_path: str, sequence_name: str, width: int, height: int,
                 music_s: Optional[float] = None, vo_s: Optional[float] = None,
                 luts: Optional[dict[str, str]] = None, frame: Optional[dict] = None) -> dict:
    text = build_ad_xml(plan, clips, sequence_name, width, height, music_s, vo_s, luts, frame)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, out_path)
    return {"path": os.path.abspath(out_path), "shots": sum(1 for a in plan.shots if a.status != "missing"),
            "missing": sum(1 for a in plan.shots if a.status == "missing")}


def ad_sequence_name(script_path: str, target_s: Optional[float], when: Optional[str] = None) -> str:
    import datetime as _dt
    base = os.path.splitext(os.path.basename(script_path.replace("\\", "/")))[0]
    ts = when or _dt.datetime.now().strftime("%Y-%m-%d %H%M%S")
    return f"AutoSync Ad - {base}{f' - {target_s:g}s' if target_s else ''} - {ts}"
