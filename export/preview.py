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


def _shot_filter(shot: Shot, w: int, h: int, fps: Fraction, lut: Optional[str]) -> str:
    # HD footage is BT.709; untagged files would otherwise be read as BT.601 (FFmpeg default).
    f = [f"scale={w}:{h}:force_original_aspect_ratio=decrease:in_color_matrix=bt709:out_color_matrix=bt709",
         f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2", "setsar=1", f"fps={fps.numerator}/{fps.denominator}"]
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


def render_preview(result: SyncResult, plan: EditPlan, out_path: str, width: int = 1280,
                   luts: Optional[dict[str, str]] = None, workers: int = 4,
                   progress: Optional[Callable[[int, int], None]] = None) -> dict:
    ffmpeg = find_tool("ffmpeg")
    encoder = pick_encoder(ffmpeg)
    fps = Fraction(plan.fps_num, plan.fps_den)
    height = int(round(width * result.sequence.height / result.sequence.width / 2)) * 2
    clips = {c.clip_id: c for c in result.clips}
    luts = luts or {}
    tmp = tempfile.mkdtemp(prefix="autosync_preview_")
    try:
        parts: list[str] = []
        jobs = []
        for i, s in enumerate(plan.shots):
            part = os.path.join(tmp, f"shot{i:05d}.mp4")
            parts.append(part)
            n = s.end - s.start
            if s.kind == "gap" or not s.clip_id:
                cmd = [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                       f"color=c=black:s={width}x{height}:r={fps.numerator}/{fps.denominator}",
                       "-frames:v", str(n)] + _enc_args(encoder) + ["-an", part]
            else:
                c = clips[s.clip_id]
                start_s = float(Fraction(s.src_in) / fps)
                cmd = [ffmpeg, "-y", "-v", "error", "-ss", f"{start_s:.6f}", "-i", c.path,
                       "-vf", _shot_filter(s, width, height, fps, luts.get(s.clip_id)),
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
        total_s = float(Fraction(plan.duration_frames) / fps)
        out_tmp = out_path + ".tmp.mp4"
        cmd = [ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listing, "-i", result.master.path,
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
