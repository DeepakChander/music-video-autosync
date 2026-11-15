"""First-cut outputs: the preview MP4 shows exactly the planned take and source frame on every
frame (lip sync holds), and the XML carries the same cuts, markers and effects."""

import os
import subprocess
import xml.etree.ElementTree as ET
from fractions import Fraction

import numpy as np
import pytest
from scipy.io import wavfile
from scipy.signal import resample_poly

from engine.edit import EditSettings, build_plan, validate_plan
from engine.music import analyse_music
from engine.result import ClipResult, Diagnostics, MasterInfo, Reason, SequenceInfo, Status, SyncResult
from engine.settings import EngineSettings
from engine.timebase import snap_seconds_to_frame_ticks
from export.edit_xml import build_edit_xml
from export.preview import render_preview
from tests.conftest import needs_ffmpeg
from tools.synth_song import make_song

FPS = Fraction(24000, 1001)
SONG_S = 40.0
W, H = 320, 180


def _coded_take(ffmpeg, path, take_index, seconds):
    """Solid frames whose colour encodes (take, frame number): R = take*40, G = (n%16)*16, B = (n//16%16)*16.
    Steps of 16 levels: 4:2:0 encoding moves a channel by up to ~4 levels."""
    expr = f"r='{take_index * 40}':g='mod(N,16)*16':b='mod(floor(N/16),16)*16'"
    # Written as tagged BT.709 like HD camera files (the preview reads untagged footage as BT.709).
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=24000/1001",
                    "-vf", f"format=rgb24,geq={expr},scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-t", str(seconds), "-c:v", "mpeg4", "-q:v", "1", path], check=True)


@pytest.fixture(scope="module")
def shoot(tmp_path_factory, ffmpeg_bin):
    d = tmp_path_factory.mktemp("cut")
    song = make_song(seed=3, bpm=120.0, layout="short")
    audio = song.audio[: int(SONG_S * song.sr)]
    master = str(d / "song.wav")
    wavfile.write(master, song.sr, (audio * 32767).astype(np.int16))
    clips = []
    pre = {1: 0.0, 2: 0.5, 3: 0.0, 4: 1.25}  # take 2 and 4 start before the song (pre-roll)
    for k in range(1, 5):
        p = str(d / f"take{k}.mov")
        _coded_take(ffmpeg_bin, p, k, SONG_S + pre[k] + 1.0)
        ticks, _ = snap_seconds_to_frame_ticks(0.0, FPS)
        clips.append(ClipResult(clip_id=f"take{k}", path=p, name=f"take{k}.mov", duration_s=SONG_S + pre[k] + 1.0,
                                source_fps="24000/1001", has_audio=True, status=Status.HIGH, song_position_s=0.0,
                                song_position_ticks=str(ticks), source_in_s=pre[k], rate=1.0, video_track=k,
                                diagnostics=Diagnostics(raw_song_position_s=-pre[k])))
    sp = str(d / "story.mov")
    _coded_take(ffmpeg_bin, sp, 6, 20.0)
    clips.append(ClipResult(clip_id="story", path=sp, name="story.mov", duration_s=20.0, source_fps="24000/1001",
                            has_audio=False, status=Status.UNSYNCED, reason=Reason.NO_AUDIO))
    res = SyncResult(engine_version="t", created_at="t", settings={}, clips=clips,
                     master=MasterInfo(path=master, sha256="x", duration_s=SONG_S, sample_rate=song.sr),
                     sequence=SequenceInfo(fps_num=24000, fps_den=1001, width=1920, height=1080))
    mm = analyse_music(resample_poly(audio, 1, 3).astype(np.float32), EngineSettings())
    return d, res, mm


def _frames_rgb(ffmpeg, path):
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-vf", "scale=8:8:in_color_matrix=bt709,format=rgb24",
                          "-f", "rawvideo", "-"],
                         check=True, stdout=subprocess.PIPE).stdout
    a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 8, 8, 3)
    return a[:, 2:6, 2:6, :].reshape(a.shape[0], -1, 3).mean(axis=1)


@needs_ffmpeg
def test_preview_shows_the_planned_take_and_source_frame_on_every_frame(shoot, ffmpeg_bin):
    d, res, mm = shoot
    plan = build_plan(res, mm, EditSettings(style="performance_story", effects=False))
    assert validate_plan(plan, res, mm) == []
    assert any(s.kind == "story" for s in plan.shots)
    info = render_preview(res, plan, str(d / "preview.mp4"), width=W)
    rgb = _frames_rgb(ffmpeg_bin, info["path"])
    assert abs(len(rgb) - plan.duration_frames) <= 1
    ids = {c.clip_id: (6 if c.clip_id == "story" else int(c.clip_id[-1])) for c in res.clips}
    bad = 0
    for s in plan.shots:
        for f in range(s.start, min(s.end, len(rgb))):
            r, g, b = rgb[f]
            take = int(round(r / 40))
            n = int(round(g / 16)) % 16 + 16 * (int(round(b / 16)) % 16)
            want_n = (s.src_in + (f - s.start)) % 256
            if take != ids[s.clip_id] or n != want_n:
                bad += 1
    assert bad == 0, f"{bad} frames show the wrong take or source frame"


@needs_ffmpeg
def test_preview_with_effects_renders_full_length(shoot, ffmpeg_bin):
    d, res, mm = shoot
    plan = build_plan(res, mm, EditSettings(style="hype"))
    info = render_preview(res, plan, str(d / "hype.mp4"), width=W)
    out = subprocess.run([ffmpeg_bin.replace("ffmpeg", "ffprobe"), "-v", "error", "-count_frames", "-select_streams",
                          "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", info["path"]],
                         check=True, stdout=subprocess.PIPE).stdout.decode().strip()
    assert abs(int(out) - plan.duration_frames) <= 1


def test_xml_carries_the_plan(shoot):
    d, res, mm = shoot
    plan = build_plan(res, mm, EditSettings(style="hype"))
    root = ET.fromstring(build_edit_xml(res, plan, "AutoSync Cut - test").split("\n", 2)[2])
    seq = root.find("sequence")
    items = seq.findall("./media/video/track/clipitem")
    shots = [s for s in plan.shots if s.kind != "gap"]
    assert len(items) == len(shots)
    for it, s in zip(items, shots):
        assert (int(it.findtext("start")), int(it.findtext("end"))) == (s.start, s.end)
        assert (int(it.findtext("in")), int(it.findtext("out"))) == (s.src_in, s.src_out)
    assert [m.findtext("name") for m in seq.findall("marker")] == [m.name for m in plan.markers]
    n_punch = sum(1 for s in shots for e in s.effects if e.kind == "punch_in")
    assert len(seq.findall(".//clipitem/filter/effect[name='Basic Motion']")) == n_punch
    flashes = sum(1 for i, s in enumerate(plan.shots) for e in s.effects if e.kind == "flash" and i > 0 and plan.shots[i - 1].kind != "gap")
    assert len(seq.findall("./media/video/track/transitionitem")) == flashes
    master = seq.find("./media/audio/track/clipitem")
    assert master.findtext("name") == "song"
