import subprocess

import pytest

from engine.decode import decode_mono
from engine.probe import ProbeError, probe
from tests.conftest import needs_ffmpeg


def _mk(ffmpeg, path, audio=True, fps="25", dur=2.0, itsoffset=None):
    cmd = [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=64x36:rate={fps}"]
    if audio:
        if itsoffset:
            cmd += ["-itsoffset", str(itsoffset)]
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    cmd += ["-t", str(dur), "-c:v", "mpeg4"]
    if audio:
        cmd += ["-c:a", "pcm_s16le"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)


@needs_ffmpeg
def test_probe_video_with_audio(tmp_path, ffmpeg_bin):
    p = tmp_path / "a.mov"
    _mk(ffmpeg_bin, p, fps="24000/1001")
    info = probe(str(p))
    assert info.has_video and info.has_audio
    assert info.video_fps == "24000/1001"
    assert abs(info.duration_s - 2.0) < 0.1


@needs_ffmpeg
def test_probe_no_audio(tmp_path, ffmpeg_bin):
    p = tmp_path / "b.mov"
    _mk(ffmpeg_bin, p, audio=False)
    assert probe(str(p)).has_audio is False


@needs_ffmpeg
def test_probe_corrupt_raises(tmp_path):
    p = tmp_path / "c.mov"
    p.write_bytes(b"\x00\x00\x00\x18ftypqt  " + b"\x01" * 2000)
    with pytest.raises(ProbeError):
        probe(str(p))


@needs_ffmpeg
def test_decode_rate_and_length(tmp_path, ffmpeg_bin):
    p = tmp_path / "d.mov"
    _mk(ffmpeg_bin, p, dur=3.0)
    x = decode_mono(str(p), 16000)
    assert abs(x.size / 16000 - 3.0) < 0.05
    assert x.dtype.name == "float32"


@needs_ffmpeg
def test_av_offset_from_container(tmp_path, ffmpeg_bin):
    p = tmp_path / "e.mov"
    _mk(ffmpeg_bin, p, dur=3.0, itsoffset=0.25)
    assert abs(probe(str(p)).av_offset_s - 0.25) < 0.01
