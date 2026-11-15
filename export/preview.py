"""Render an edit plan to a preview MP4 with FFmpeg (separate process, LGPL build is enough).

Each shot is cut from its source at the plan's exact frames, scaled into the preview frame, given
its effects (punch-in, white flash; dissolves are rendered as cuts) and optional colour LUT, then
the shots are joined and the song is laid underneath. The preview is for watching and sharing the
first cut; the editable version is the Premiere sequence (export/edit_xml.py).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from typing import Callable, Optional

from engine.edit import EditPlan, Shot
from engine.probe import find_tool, run_tool
from engine.result import SyncResult

_ENCODERS = ["libx264", "h264_videotoolbox", "h264_mf", "libopenh264", "mpeg4"]


class PreviewError(RuntimeError):
    pass


def pick_encoder(ffmpeg: str) -> str:
    out = run_tool([ffmpeg, "-hide_banner", "-encoders"], timeout=30).stdout.decode("utf-8", "replace")
    names = {line.split()[1] for line in out.splitlines() if len(line.split()) > 1 and line.startswith(" V")}
    for e in _ENCODERS:
        if e in names:
            return e
    raise PreviewError("no usable video encoder in this FFmpeg build")


_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


def _enc_args(encoder: str) -> list[str]:
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"] + _TAGS
    if encoder == "mpeg4":
        return ["-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p"] + _TAGS
    return ["-c:v", encoder, "-b:v", "8M", "-pix_fmt", "yuv420p"] + _TAGS


def _shot_filter(shot: Shot, w: int, h: int, fps: Fraction, lut: Optional[str],
                 centre: Optional[tuple[float, float]] = None, speed: float = 1.0) -> str:
    # HD footage is BT.709; untagged files would otherwise be read as BT.601 (FFmpeg default).
    if centre is not None:  # social version: cover the frame, crop around the subject
        from export.reframe import reframe_vf
        f = [reframe_vf(w, h, centre[0], centre[1])]
    else:
        f = [f"scale={w}:{h}:force_original_aspect_ratio=decrease:in_color_matrix=bt709:out_color_matrix=bt709",
             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2", "setsar=1"]
    if abs(speed - 1.0) > 1e-9:  # slow motion: stretch the source's timestamps
        f.append(f"setpts={1.0 / speed:.6f}*PTS")
    f.append(f"fps={fps.numerator}/{fps.denominator}")
    if lut:
        f.append(f"lut3d=file='{lut.replace(chr(92), '/').replace(':', chr(92) + ':')}'")
    for e in shot.effects:
        if e.kind == "punch_in":
            z = e.amount / 100.0
            n = max(1, e.frames)
            # zoom from 1.0 to z over n frames, then hold, centred (one output frame per input frame)
            f.append(f"zoompan=z='if(lt(on,{n}),1+({z}-1)*on/{n},{z})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                     f":d=1:s={w}x{h}:fps={fps.numerator}/{fps.denominator}")
        if e.kind == "flash":
            dur = e.frames / float(fps)
            f.append(f"fade=t=in:st=0:d={dur:.4f}:color=white")
    return ",".join(f)


def _render_picture(segments: list[tuple[Optional[str], int, int, Optional[str]]], fps: Fraction, width: int,
                    height: int, encoder: str, tmp: str, workers: int,
                    progress: Optional[Callable[[int, int], None]]) -> str:
    """Render picture-only segments [(path or None for black, src_in_frame, frames, -vf filter)] and
    join them; returns the joined file (video only)."""
    ffmpeg = find_tool("ffmpeg")
    parts: list[str] = []
    jobs = []
    for i, (path, src_in, n, vf) in enumerate(segments):
        part = os.path.join(tmp, f"shot{i:05d}.mp4")
        parts.append(part)
        if path is None:
            cmd = [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                   f"color=c=black:s={width}x{height}:r={fps.numerator}/{fps.denominator}",
                   "-frames:v", str(n)] + _enc_args(encoder) + ["-an", part]
        else:
            start_s = float(Fraction(src_in) / fps)
            cmd = [ffmpeg, "-y", "-v", "error", "-ss", f"{start_s:.6f}", "-i", path, "-vf", vf,
                   "-frames:v", str(n)] + _enc_args(encoder) + ["-an", part]
        jobs.append(cmd)
    done = [0]

    def run(cmd):
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        done[0] += 1
        if progress:
            progress(done[0], len(jobs))
        if r.returncode != 0:
            raise PreviewError(r.stderr.decode("utf-8", "replace")[-400:])

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(run, jobs))
    listing = os.path.join(tmp, "list.txt")
    with open(listing, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    joined = os.path.join(tmp, "picture.mp4")
    r = subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", joined],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise PreviewError(r.stderr.decode("utf-8", "replace")[-400:])
    return joined


def render_preview(result: SyncResult, plan: EditPlan, out_path: str, width: int = 1280,
                   luts: Optional[dict[str, str]] = None, workers: int = 4,
                   progress: Optional[Callable[[int, int], None]] = None,
                   aspect: Optional[tuple[int, int]] = None, centres: Optional[list[tuple[float, float]]] = None) -> dict:
    """`aspect` = (w, h) of a social version (e.g. 1080x1920) scaled to `width`; `centres` = the
    source point to keep centred, per shot (export/reframe.py)."""
    ffmpeg = find_tool("ffmpeg")
    encoder = pick_encoder(ffmpeg)
    fps = Fraction(plan.fps_num, plan.fps_den)
    ar_w, ar_h = aspect or (result.sequence.width, result.sequence.height)
    height = int(round(width * ar_h / ar_w / 2)) * 2
    clips = {c.clip_id: c for c in result.clips}
    luts = luts or {}
    tmp = tempfile.mkdtemp(prefix="autosync_preview_")
    try:
        segs = []
        for i, s in enumerate(plan.shots):
            n = s.end - s.start
            if s.kind == "gap" or not s.clip_id:
                segs.append((None, 0, n, None))
            else:
                ctr = centres[i] if (aspect and centres) else ((0.5, 0.5) if aspect else None)
                speed = getattr(s, "speed", 1.0) or 1.0
                segs.append((clips[s.clip_id].path, s.src_in, n,
                             _shot_filter(s, width, height, fps, luts.get(s.clip_id), ctr, speed)))
        picture = _render_picture(segs, fps, width, height, encoder, tmp, workers, progress)
        total_s = float(Fraction(plan.duration_frames) / fps)
        out_tmp = out_path + ".tmp.mp4"
        cmd = [ffmpeg, "-y", "-v", "error", "-i", picture, "-i", result.master.path,
               "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
               "-t", f"{total_s:.6f}", "-movflags", "+faststart", out_tmp]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode != 0:
            raise PreviewError(r.stderr.decode("utf-8", "replace")[-400:])
        os.replace(out_tmp, out_path)
        return {"path": os.path.abspath(out_path), "encoder": encoder, "shots": len(plan.shots),
                "width": width, "height": height, "seconds": round(total_s, 3)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def render_ad_preview(plan, clip_paths: dict[str, str], out_path: str, width: int = 1280, height: int = 720,
                      luts: Optional[dict[str, str]] = None, music_gain_db: float = -12.0, workers: int = 4,
                      progress: Optional[Callable[[int, int], None]] = None,
                      centres: Optional[list[tuple[float, float]]] = None) -> dict:
    """`centres` given = a social version: each shot covers width x height around its centre point."""
    """Ad preview: shots in script order (black for missing shots), each dialogue shot's own
    sound, the VO recording per line, the music bed underneath."""
    ffmpeg = find_tool("ffmpeg")
    encoder = pick_encoder(ffmpeg)
    fps = Fraction(plan.fps_num, plan.fps_den)
    luts = luts or {}
    tmp = tempfile.mkdtemp(prefix="autosync_adpreview_")
    try:
        segs = []
        for k, a in enumerate(plan.shots):
            n = a.end - a.start
            if a.status == "missing" or not a.clip_id:
                segs.append((None, 0, n, None))
            else:
                if centres is not None:
                    from export.reframe import reframe_vf
                    vf = [reframe_vf(width, height, centres[k][0], centres[k][1]), f"fps={fps.numerator}/{fps.denominator}"]
                else:
                    vf = [f"scale={width}:{height}:force_original_aspect_ratio=decrease:in_color_matrix=bt709:out_color_matrix=bt709",
                          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2", "setsar=1", f"fps={fps.numerator}/{fps.denominator}"]
                lut = luts.get(a.clip_id)
                if lut:
                    vf.append(f"lut3d=file='{lut.replace(chr(92), '/').replace(':', chr(92) + ':')}'")
                segs.append((clip_paths[a.clip_id], a.src_in, n, ",".join(vf)))
        picture = _render_picture(segs, fps, width, height, encoder, tmp, workers, progress)
        total_s = float(Fraction(plan.duration_frames) / fps)
        # Audio: one input per sounding piece, delayed to its place, mixed without normalisation.
        inputs, chains = [], []
        def add(path, src_s, dur_s, at_s, gain_db=0.0):
            k = len(chains) + 1  # input index (0 is the picture) and label number
            inputs.extend(["-i", path])
            ms = int(round(at_s * 1000))
            chains.append(f"[{k}:a]atrim=start={src_s:.4f}:duration={dur_s:.4f},asetpts=PTS-STARTPTS,"
                          f"aformat=sample_rates=48000:channel_layouts=stereo,volume={gain_db}dB,adelay={ms}|{ms}[a{k}]")
        for a in plan.shots:
            if a.status != "missing" and a.clip_id and a.kind == "dialogue" and a.line_score is not None:
                add(clip_paths[a.clip_id], a.src_in / float(fps), (a.end - a.start) / float(fps), a.start / float(fps))
            if plan.vo_path and a.vo_in_s is not None and a.vo_out_s is not None:
                add(plan.vo_path, a.vo_in_s, a.vo_out_s - a.vo_in_s, a.start / float(fps))
        if plan.music_path:
            add(plan.music_path, 0.0, total_s, 0.0, music_gain_db)
        out_tmp = out_path + ".tmp.mp4"
        # A silent bed of exactly the cut's length is input 1 and sets the mix length
        # (amix duration=first): every piece is delayed onto it.
        base = ["-f", "lavfi", "-t", f"{total_s:.6f}", "-i", "anullsrc=r=48000:cl=stereo"]
        shifted = [c.replace(f"[{k}:a]", f"[{k + 1}:a]", 1) for k, c in enumerate(chains, start=1)]
        labels = "[1:a]" + "".join(f"[a{k}]" for k in range(1, len(chains) + 1))
        fc = (";".join(shifted) + ";" if shifted else "") + f"{labels}amix=inputs={len(chains) + 1}:normalize=0:duration=first[mix]"
        cmd = [ffmpeg, "-y", "-v", "error", "-nostdin", "-i", picture] + base + inputs
        cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[mix]", "-c:a", "aac", "-b:a", "192k",
                "-c:v", "copy", "-t", f"{total_s:.6f}", "-movflags", "+faststart", out_tmp]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                           timeout=max(120.0, 10 * total_s))
        if r.returncode != 0:
            raise PreviewError(r.stderr.decode("utf-8", "replace")[-400:])
        os.replace(out_tmp, out_path)
        return {"path": os.path.abspath(out_path), "encoder": encoder, "shots": len(plan.shots),
                "width": width, "height": height, "seconds": round(total_s, 3)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
