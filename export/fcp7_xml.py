"""FCP7 XML (xmeml version 5) writer for placement adapter B.

Reads positions from sync_result.json; never recomputes them (spec section 3).

Rules applied (sources in docs/PREMIERE_API_NOTES.md item 12):
  * Apple xmeml spec: clipitem needs name/duration/rate/start/end; start and in are 0-based,
    end and out are exclusive; rate = timebase + ntsc (23.976 = 24/TRUE, 29.97 = 30/TRUE ...).
  * pathurl starts with file://localhost and percent-escapes illegal characters; on Windows the
    drive colon is escaped too (file://localhost/D%3a/...) -- SECONDARY evidence, PPro 26.5.
  * Mixed frame rates: clipitem in/out are written in SEQUENCE frames -- SECONDARY evidence:
    Premiere read clip-rate in/out as sequence frames.
  * Track mute is NOT written: no source defines how Premiere maps a track's <enabled> on import.
    The panel mutes scratch tracks with AudioTrack.setMute after import.
Premiere's acceptance of this file is checked by the panel's placement self-test (readback).
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional
from urllib.parse import quote
from xml.sax.saxutils import escape

from engine.overrides import placeable
from engine.result import ClipResult, SyncResult
from engine.timebase import TICKS_PER_SECOND, parse_rate, ticks_per_frame


class ExportError(ValueError):
    pass


@dataclass
class XmlOptions:
    sequence_name: str
    keep_scratch_audio: bool = True
    place_low: bool = False


def timebase_ntsc(fps: Fraction) -> tuple[int, bool]:
    """xmeml rate for a frame rate. Raises for rates xmeml cannot express."""
    if fps.denominator == 1:
        return fps.numerator, False
    if fps.denominator == 1001 and fps.numerator % 1000 == 0:
        return fps.numerator // 1000, True
    approx = float(fps)
    for base in (24, 30, 60, 48, 120):
        if abs(approx - base * 1000 / 1001) < 1e-3:
            return base, True
    if abs(approx - round(approx)) < 1e-6:
        return int(round(approx)), False
    raise ExportError(f"frame rate {fps} cannot be expressed as an xmeml timebase")


def _rate(fps: Fraction) -> str:
    tb, ntsc = timebase_ntsc(fps)
    return f"<rate><timebase>{tb}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"


def pathurl(path: str) -> str:
    """file://localhost URL. Windows 'D:\\a b\\c.mov' -> file://localhost/D%3a/a%20b/c.mov"""
    p = path.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    if m:
        drive, rest = m.group(1), m.group(2)
        return "file://localhost/" + drive + "%3a/" + "/".join(quote(seg, safe="") for seg in rest.split("/"))
    if p.startswith("//"):  # UNC \\server\share\x -> file://server/share/x
        parts = p[2:].split("/")
        return "file://" + parts[0] + "/" + "/".join(quote(seg, safe="") for seg in parts[1:])
    return "file://localhost" + "/".join(quote(seg, safe="") for seg in p.split("/"))


def _frames(seconds: float, fps: Fraction) -> int:
    return int(math.floor(Fraction(seconds).limit_denominator(10**9) * fps + Fraction(1, 2)))


@dataclass
class _Placed:
    clip: ClipResult
    track: int
    start: int  # sequence frames
    in_: int
    out: int
    total: int


def plan(result: SyncResult, opts: XmlOptions) -> list[_Placed]:
    fps = Fraction(result.sequence.fps_num, result.sequence.fps_den)
    tpf = ticks_per_frame(fps)
    out: list[_Placed] = []
    for c in result.clips:
        if not placeable(c, opts.place_low):
            continue
        if c.override is not None:
            ticks, track, in_s = int(c.override.song_position_ticks), c.override.video_track, 0.0
        else:
            ticks, track = int(c.song_position_ticks), c.video_track
            in_s = 0.0
            raw = c.diagnostics.raw_song_position_s
            if c.source_in_s > 0 and raw is not None:
                # Derive the in-point from the snapped start so clip time 0 sits at the raw
                # (pre-roll, negative) song position: in = (start - raw) / rate.
                in_s = (ticks / TICKS_PER_SECOND - raw) / (c.rate or 1.0)
        if track is None:
            raise ExportError(f"{c.name}: no video track assigned")
        start = Fraction(ticks) / tpf
        if start.denominator != 1:
            raise ExportError(f"{c.name}: position {ticks} ticks is not on a sequence frame")
        total = max(1, _frames(c.duration_s, fps))
        in_f = min(total - 1, max(0, _frames(in_s, fps)))
        out.append(_Placed(c, track, int(start), in_f, total, total))
    return out


def _file_xml(c: ClipResult, fid: str, seq_fps: Fraction) -> str:
    src = parse_rate(c.source_fps) if c.source_fps else seq_fps
    try:
        src_rate = _rate(src)
    except ExportError:
        src_rate = _rate(seq_fps)
        src = seq_fps
    dur = max(1, _frames(c.duration_s, src))
    media = "<media>"
    if c.source_fps:
        media += f"<video><duration>{dur}</duration></video>"
    if c.has_audio:
        ch = c.diagnostics.audio_channels or 2
        media += f"<audio><channelcount>{ch}</channelcount></audio>"
    media += "</media>"
    name = os.path.basename(c.path.replace("\\", "/"))
    return (f'<file id="{fid}"><name>{escape(name)}</name><pathurl>{escape(pathurl(c.path))}</pathurl>'
            f"{src_rate}<duration>{dur}</duration>{media}</file>")


def build_xml(result: SyncResult, opts: XmlOptions) -> str:
    fps = Fraction(result.sequence.fps_num, result.sequence.fps_den)
    rate = _rate(fps)
    placed = plan(result, opts)
    n_video = max((p.track for p in placed), default=1)
    master_frames = max(1, _frames(result.master.duration_s, fps))
    seq_end = max([master_frames] + [p.start + (p.out - p.in_) for p in placed])

    by_track: dict[int, list[_Placed]] = {}
    for p in sorted(placed, key=lambda p: (p.track, p.start, p.clip.name)):
        by_track.setdefault(p.track, []).append(p)
    def has_video(p: _Placed) -> bool:
        return p.clip.source_fps is not None

    def has_scratch(p: _Placed) -> bool:
        return opts.keep_scratch_audio and p.clip.has_audio

    # clipindex = 1-based position of a clipitem within its own track (video and audio differ
    # when some clips have no audio or no video).
    v_index: dict[str, int] = {}
    a_index: dict[str, int] = {}
    for t, items in by_track.items():
        for i, p in enumerate([p for p in items if has_video(p)], start=1):
            v_index[p.clip.clip_id] = i
        for i, p in enumerate([p for p in items if has_scratch(p)], start=1):
            a_index[p.clip.clip_id] = i

    def links(p: _Placed) -> str:
        cid = p.clip.clip_id
        s = ""
        if has_video(p):
            s += (f"<link><linkclipref>v-{cid}</linkclipref><mediatype>video</mediatype>"
                  f"<trackindex>{p.track}</trackindex><clipindex>{v_index[cid]}</clipindex></link>")
        if has_scratch(p):
            s += (f"<link><linkclipref>a-{cid}</linkclipref><mediatype>audio</mediatype>"
                  f"<trackindex>{p.track + 1}</trackindex><clipindex>{a_index[cid]}</clipindex>"
                  f"<groupindex>1</groupindex></link>")
        return s

    def clipitem(p: _Placed, kind: str) -> str:
        c = p.clip
        fid = f"file-{c.clip_id}"
        full_file = kind == "v" or not has_video(p)  # first occurrence carries the definition
        file_xml = _file_xml(c, fid, fps) if full_file else f'<file id="{fid}"/>'
        body = (f'<clipitem id="{kind}-{c.clip_id}"><name>{escape(c.name)}</name><enabled>TRUE</enabled>'
                f"<duration>{p.total}</duration>{rate}<start>{p.start}</start>"
                f"<end>{p.start + (p.out - p.in_)}</end><in>{p.in_}</in><out>{p.out}</out>{file_xml}")
        if kind == "a":
            body += "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>"
        body += links(p) + "</clipitem>"
        return body

    video_tracks = []
    for t in range(1, n_video + 1):
        items = "".join(clipitem(p, "v") for p in by_track.get(t, []) if has_video(p))
        video_tracks.append(f"<track>{items}<enabled>TRUE</enabled><locked>FALSE</locked></track>")

    master_name = os.path.basename(result.master.path.replace("\\", "/"))
    master_item = (
        f'<clipitem id="master"><name>{escape(os.path.splitext(master_name)[0])}</name><enabled>TRUE</enabled>'
        f"<duration>{master_frames}</duration>{rate}<start>0</start><end>{master_frames}</end>"
        f"<in>0</in><out>{master_frames}</out>"
        f'<file id="file-master"><name>{escape(master_name)}</name><pathurl>{escape(pathurl(result.master.path))}</pathurl>'
        f"{rate}<duration>{master_frames}</duration><media><audio><channelcount>2</channelcount></audio></media></file>"
        "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>")
    audio_tracks = [f'<track premiereTrackType="Stereo">{master_item}<enabled>TRUE</enabled><locked>FALSE</locked></track>']
    if opts.keep_scratch_audio:
        for t in range(1, n_video + 1):
            items = "".join(clipitem(p, "a") for p in by_track.get(t, []) if has_scratch(p))
            audio_tracks.append(f'<track premiereTrackType="Stereo">{items}<enabled>TRUE</enabled><locked>FALSE</locked></track>')

    seq = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="5">'
        f'<sequence id="sequence-1"><name>{escape(opts.sequence_name)}</name><duration>{seq_end}</duration>{rate}'
        f"<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
        "<media><video><format><samplecharacteristics>"
        f"{rate}<width>{result.sequence.width}</width><height>{result.sequence.height}</height>"
        "<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>"
        + "".join(video_tracks) + "</video><audio>" + "".join(audio_tracks) + "</audio></media></sequence></xmeml>\n"
    )
    return seq


def write_xml(result: SyncResult, out_path: str, opts: XmlOptions) -> dict:
    text = build_xml(result, opts)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, out_path)
    placed = plan(result, opts)
    return {"path": os.path.abspath(out_path), "clips_placed": len(placed),
            "video_tracks": max((p.track for p in placed), default=0),
            "scratch_audio_tracks": max((p.track for p in placed), default=0) if opts.keep_scratch_audio else 0}


def sequence_name(song_path: str, when: Optional[str] = None) -> str:
    import datetime as _dt
    song = os.path.splitext(os.path.basename(song_path.replace("\\", "/")))[0]
    ts = when or _dt.datetime.now().strftime("%Y-%m-%d %H%M%S")
    return f"AutoSync - {song} - {ts}"
