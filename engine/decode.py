"""ffmpeg -> mono float32 PCM at the analysis rate."""

from __future__ import annotations

from typing import Optional

import numpy as np

from .probe import clean_message, find_tool, run_tool


class DecodeError(RuntimeError):
    pass


def decode_mono(path: str, sr: int, ffmpeg: Optional[str] = None, timeout: float = 600.0) -> np.ndarray:
    """Decode the first audio stream, downmixed to mono, resampled to `sr`, as float32.

    Sample 0 of the output is the first decoded audio sample of the stream (after container
    edit lists, which FFmpeg applies -- this removes AAC encoder priming in MP4/MOV).
    """
    ffmpeg = ffmpeg or find_tool("ffmpeg")
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0", "-vn", "-sn", "-dn",
           "-ac", "1", "-ar", str(sr), "-c:a", "pcm_f32le", "-f", "f32le", "-"]
    proc = run_tool(cmd, timeout=timeout)
    if proc.returncode != 0:
        msg = clean_message(proc.stderr.decode("utf-8", "replace"))
        raise DecodeError(msg.splitlines()[-1] if msg else "ffmpeg decode failed")
    audio = np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32, copy=True)
    if audio.size == 0:
        raise DecodeError("decoded audio is empty")
    if not np.all(np.isfinite(audio)):
        audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    return audio


def rms_dbfs(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -200.0
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    return 20.0 * np.log10(max(rms, 1e-10))
