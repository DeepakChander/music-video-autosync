"""ffprobe wrapper. FFmpeg is always called as a subprocess (keeps LGPL licensing clean)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

# Never open a console window for child processes on Windows.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class FFmpegNotFound(RuntimeError):
    pass


class ProbeError(RuntimeError):
    pass


def _candidate_dirs() -> list[str]:
    dirs = []
    env = os.environ.get("AUTOSYNC_FFMPEG_DIR")
    if env:
        dirs.append(env)
    # PyInstaller bundle: ffmpeg shipped in <bundle>/ffmpeg/
    base = getattr(sys, "_MEIPASS", None)
    if base:
        dirs.append(os.path.join(base, "ffmpeg"))
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    dirs.append(os.path.join(exe_dir, "ffmpeg"))
    return dirs


def find_tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    for d in _candidate_dirs():
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    found = shutil.which(name)
    if found:
        return found
    raise FFmpegNotFound(f"{name} not found (set AUTOSYNC_FFMPEG_DIR or install FFmpeg)")


_ADDR = re.compile(r"\s*@\s*0x[0-9a-fA-F]+")


def clean_message(text: str) -> str:
    """FFmpeg prefixes messages with '[demuxer @ 0x7f...]'. Drop the address so results stay
    deterministic (rule 8)."""
    return _ADDR.sub("", text).strip()


def run_tool(cmd: list[str], timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, timeout=timeout, creationflags=_NO_WINDOW)


@dataclass
class MediaInfo:
    path: str
    duration_s: float
    has_video: bool
    has_audio: bool
    video_fps: Optional[str] = None
    video_start_s: float = 0.0
    audio_start_s: float = 0.0
    audio_channels: int = 0
    audio_sample_rate: int = 0
    width: int = 0
    height: int = 0
    audio_codec: Optional[str] = None
    video_codec: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def av_offset_s(self) -> float:
        """Audio start minus video start (container timestamps). 0 for audio-only files."""
        if self.has_video and self.has_audio:
            return self.audio_start_s - self.video_start_s
        return 0.0


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def probe(path: str, ffprobe: Optional[str] = None, timeout: float = 60.0) -> MediaInfo:
    ffprobe = ffprobe or find_tool("ffprobe")
    cmd = [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    proc = run_tool(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise ProbeError(clean_message(proc.stderr.decode("utf-8", "replace")) or "ffprobe failed")
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise ProbeError(f"ffprobe returned invalid JSON: {e}") from e
    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    video = None
    audio = None
    for s in streams:
        ctype = s.get("codec_type")
        disp = s.get("disposition") or {}
        if ctype == "video" and video is None and not disp.get("attached_pic"):
            video = s
        elif ctype == "audio" and audio is None:
            audio = s
    if video is None and audio is None:
        raise ProbeError("no audio or video stream")

    if video is not None:
        duration = _f(video.get("duration"), 0.0) or _f(fmt.get("duration"), 0.0)
    else:
        duration = _f(audio.get("duration"), 0.0) or _f(fmt.get("duration"), 0.0)

    fps = None
    if video is not None:
        for key in ("r_frame_rate", "avg_frame_rate"):
            v = video.get(key)
            if v and v not in ("0/0", "0"):
                fps = v
                break

    return MediaInfo(
        path=path,
        duration_s=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        video_fps=fps,
        video_start_s=_f(video.get("start_time")) if video is not None else 0.0,
        audio_start_s=_f(audio.get("start_time")) if audio is not None else 0.0,
        audio_channels=int(audio.get("channels") or 0) if audio is not None else 0,
        audio_sample_rate=int(_f(audio.get("sample_rate"))) if audio is not None else 0,
        width=int(video.get("width") or 0) if video is not None else 0,
        height=int(video.get("height") or 0) if video is not None else 0,
        audio_codec=audio.get("codec_name") if audio is not None else None,
        video_codec=video.get("codec_name") if video is not None else None,
        raw=data,
    )
