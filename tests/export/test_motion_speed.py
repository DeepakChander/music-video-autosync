"""Speed (Time Remap), social-version framing (Basic Motion), Lumetri LUT install, 9:16 preview."""

import os
import subprocess
import xml.etree.ElementTree as ET
from fractions import Fraction

import numpy as np
import pytest
from scipy.signal import resample_poly

from engine.colour import ColourPlan, TakeColour, build_lut, install_luts, write_cube
from engine.edit import EditSettings, build_plan, validate_plan
from engine.music import analyse_music
from engine.settings import EngineSettings
from export.edit_xml import build_edit_xml
from export.reframe import ASPECTS, crop_window
from export.xml_motion import reframe_motion
from tests.conftest import needs_ffmpeg
from tests.unit.test_edit import FPS, clip, result
from tools.synth_song import make_song


@pytest.fixture(scope="module")
def song():
    s = make_song(seed=1, bpm=112.0, layout="pop")
    return s, analyse_music(resample_poly(s.audio, 1, 3).astype(np.float32), EngineSettings())


def test_reframe_formula_matches_the_worked_example():
    # 1920x1080 into 1080x1920, face at x = 0.7: scale 177.78, horiz -0.3556 (Position.x = -142.7 px)
    sc, hz, vt = reframe_motion(1920, 1080, 1080, 1920, 0.7, 0.5)
    assert sc == pytest.approx(177.7778, abs=1e-3) and hz == pytest.approx(-0.355556, abs=1e-5) and vt == 0
    assert 540 + hz * 1920 == pytest.approx(-142.7, abs=0.1)
    # clamped so no black edge: at most (1920*s - 1080) / (2*1920) = 0.6076
    _, hz2, _ = reframe_motion(1920, 1080, 1080, 1920, 0.0, 0.5)
    assert hz2 == pytest.approx(0.607639, abs=1e-5)
    s, x, y = crop_window(1920, 1080, 1080, 1920, 0.7, 0.5)
    assert x == pytest.approx(0.7 * 1920 * s - 540) and y == 0


def test_slow_motion_shot_is_written_as_time_remap_in_retimed_frames(song):
    s, mm = song
    d = s.duration_s
    res = result([clip("A", 0.0, d + 1), clip("B", 0.0, d + 1), clip("slow", 0.0, d / 2 + 1, rate=2.0)], d)
    plan = build_plan(res, mm, EditSettings(style="ballad", effects=False))
    assert validate_plan(plan, res, mm) == []
    slow_idx = [i for i, sh in enumerate(plan.shots) if sh.clip_id == "slow"]
    assert slow_idx
    root = ET.fromstring(build_edit_xml(res, plan, "t").split("\n", 2)[2])
    items = {it.get("id"): it for it in root.iter("clipitem")}
    total = round((d / 2 + 1) * float(FPS))
    for i in slow_idx:
        sh = plan.shots[i]
        it = items[f"cut-{i}"]
        a, b = int(it.findtext("in")), int(it.findtext("out"))
        assert b - a == sh.end - sh.start, "in/out in retimed frames: out - in = end - start"
        assert a == round(sh.src_in / 0.5)
        assert abs(int(it.findtext("duration")) - total * 2) <= 2
        eff = it.find("filter/effect[effectid='timeremap']")
        assert eff is not None and eff.find("parameter[parameterid='speed']").findtext("value") == "50"
        kfs = [(int(k.findtext("when")), float(k.findtext("value"))) for k in eff.find("parameter[parameterid='graphdict']").findall("keyframe")]
        assert (a, float(sh.src_in)) in kfs and (b, sh.src_in + (sh.end - sh.start) * 0.5) in kfs
    one_x = next(i for i, sh in enumerate(plan.shots) if sh.clip_id == "A")
    assert items[f"cut-{one_x}"].find("filter/effect[effectid='timeremap']") is None


def test_social_version_frames_every_shot(song):
    s, mm = song
    d = s.duration_s
    res = result([clip("A", 0.0, d + 1), clip("B", 0.0, d + 1)], d)
    plan = build_plan(res, mm, EditSettings(style="hype"))
    w, h = ASPECTS["9:16"]
    centres = [(0.7, 0.4)] * len(plan.shots)
    xml = build_edit_xml(res, plan, "t 9:16", frame={"width": w, "height": h, "centres": centres,
                                                     "sources": {"A": (1920, 1080), "B": (1920, 1080)}})
    root = ET.fromstring(xml.split("\n", 2)[2])
    fmt = root.find("sequence/media/video/format/samplecharacteristics")
    assert (fmt.findtext("width"), fmt.findtext("height")) == ("1080", "1920")
    for it in root.findall("sequence/media/video/track/clipitem"):
        eff = it.find("filter/effect[effectid='basic']")
        assert eff is not None
        ctr = eff.find("parameter[parameterid='center']/value")
        assert float(ctr.findtext("horiz")) == pytest.approx(-0.355556, abs=1e-5)
        sc = eff.find("parameter[parameterid='scale']")
        keys = [float(k.findtext("value")) for k in sc.findall("keyframe")]
        if keys:  # punch-in zooms from the framing scale
            assert keys[0] == pytest.approx(177.7778, abs=1e-3) and keys[1] == pytest.approx(177.7778 * 1.1, abs=1e-2)
        else:
            assert float(sc.findtext("value")) == pytest.approx(177.7778, abs=1e-3)


def test_luts_are_installed_where_lumetri_lists_them(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYNC_LUT_DIR", str(tmp_path / "LUTs"))
    src = tmp_path / "src"
    src.mkdir()
    a = write_cube(str(src / "A_match_warm.cube"), build_lut(None, "warm", 9), "a")
    look = write_cube(str(src / "look_warm.cube"), build_lut(None, "warm", 9), "look")
    plan = ColourPlan(hero="a", hero_name="A.mov", look="warm", folder=str(src), look_lut=look,
                      takes={"a": TakeColour(clip_id="a", name="A.mov", delta_e_before=1, delta_e_after=0, match_lut=a, final_lut=a)})
    out = install_luts(plan, "Song Master")
    assert os.path.isfile(tmp_path / "LUTs" / "Technical" / "AutoSync - Song Master - A.cube")
    assert os.path.isfile(tmp_path / "LUTs" / "Creative" / "AutoSync - warm.cube")
    assert out["restart_needed"] and out["warning"] is None
    # a second run for the same song replaces, not duplicates
    install_luts(plan, "Song Master")
    assert len(os.listdir(tmp_path / "LUTs" / "Technical")) == 1


@needs_ffmpeg
def test_vertical_preview_has_the_social_size(tmp_path, ffmpeg_bin):
    from export.preview import render_preview
    from scipy.io import wavfile
    song = make_song(seed=3, bpm=120.0, layout="short")
    audio = song.audio[: int(12 * song.sr)]
    master = str(tmp_path / "song.wav")
    wavfile.write(master, song.sr, (audio * 32767).astype(np.int16))
    take = str(tmp_path / "A.mov")
    subprocess.run([ffmpeg_bin, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=24000/1001", "-t", "13",
                    "-c:v", "mpeg4", take], check=True)
    c = clip("A", 0.0, 13.0)
    c.path = take
    c2 = clip("B", 0.0, 13.0)
    c2.path = take
    res = result([c, c2], 12.0)
    res.master.path = master
    mm = analyse_music(resample_poly(audio, 1, 3).astype(np.float32), EngineSettings())
    plan = build_plan(res, mm, EditSettings(effects=False))
    info = render_preview(res, plan, str(tmp_path / "v.mp4"), width=360, aspect=ASPECTS["9:16"],
                          centres=[(0.5, 0.5)] * len(plan.shots))
    wh = subprocess.run([ffmpeg_bin.replace("ffmpeg", "ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                         "stream=width,height", "-of", "csv=p=0", info["path"]], check=True, stdout=subprocess.PIPE).stdout.decode().strip()
    assert wh == "360,640"
