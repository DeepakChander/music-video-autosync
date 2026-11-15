"""Shot analysis on generated clips with known properties and on a public-domain portrait
(tests/fixtures/faces/portrait_nasa_jsc2003e41874.jpg: NASA official portrait, NASA photographer,
US government work, no copyright; source images.nasa.gov, id jsc2003e41874)."""

import os
import subprocess

import pytest

from engine.shots import ShotSettings, analyse_take, analyse_takes, group_setups, model_ok, shot_info_from_report
from tests.conftest import needs_ffmpeg

PORTRAIT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "faces", "portrait_nasa_jsc2003e41874.jpg")

try:
    import cv2  # noqa: F401
    HAS_CV2 = True
except Exception:
    HAS_CV2 = False


def _make(ffmpeg, path, src, vf="", seconds=4, loop=False):
    cmd = [ffmpeg, "-y", "-v", "error"]
    cmd += (["-loop", "1", "-i", src] if loop else ["-f", "lavfi", "-i", src])
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-t", str(seconds), "-r", "25", "-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(scope="module")
def clips(tmp_path_factory, ffmpeg_bin):
    d = tmp_path_factory.mktemp("shots")
    f = lambda n: str(d / n)  # noqa: E731
    return {
        "sharp": _make(ffmpeg_bin, f("sharp.mp4"), "testsrc2=s=640x360:r=25"),
        "blur": _make(ffmpeg_bin, f("blur.mp4"), "testsrc2=s=640x360:r=25", "boxblur=8:2"),
        "dark": _make(ffmpeg_bin, f("dark.mp4"), "testsrc2=s=640x360:r=25", "eq=brightness=-0.35"),
        "black": _make(ffmpeg_bin, f("black.mp4"), "color=c=black:s=640x360:r=25"),
        "still": _make(ffmpeg_bin, f("still.mp4"), "color=c=gray:s=640x360:r=25"),
        # overlay re-evaluates x every frame (drawbox evaluates it once)
        "moving": _make(ffmpeg_bin, f("moving.mp4"),
                        "color=c=gray:s=640x360:r=25[bg];color=c=white:s=80x160:r=25[b];[bg][b]overlay=x='mod(t*300,560)':y=100"),
        "setupA": _make(ffmpeg_bin, f("setupA.mp4"), "testsrc2=s=640x360:r=25"),
        "setupA2": _make(ffmpeg_bin, f("setupA2.mp4"), "testsrc2=s=640x360:r=25", "eq=brightness=0.06,noise=alls=12:allf=t"),
        "setupB": _make(ffmpeg_bin, f("setupB.mp4"), "smptebars=s=640x360:r=25"),
        # Shot sizes from the portrait (face is ~30% of the portrait's height).
        "close": _make(ffmpeg_bin, f("close.mp4"), PORTRAIT, "crop=420:520:310:170,scale=-2:360,pad=640:360:(ow-iw)/2:0,format=yuv420p", loop=True),
        "medium": _make(ffmpeg_bin, f("medium.mp4"), PORTRAIT, "scale=-2:180,pad=640:360:(ow-iw)/2:120:color=gray,format=yuv420p", loop=True),
        "wide": _make(ffmpeg_bin, f("wide.mp4"), PORTRAIT, "scale=-2:60,pad=640:360:300:250:color=gray,format=yuv420p", loop=True),
    }


@needs_ffmpeg
def test_picture_measurements_rank_as_expected(clips):
    ss = ShotSettings()
    t = {k: analyse_take(k, clips[k], ss, use_faces=False) for k in ("sharp", "blur", "dark", "black", "still", "moving")}
    mean = lambda k, f: sum(getattr(s, f) for s in t[k].samples) / len(t[k].samples)  # noqa: E731
    assert all(len(x.samples) >= 6 for x in t.values()), "2 samples per second over 4 s"
    assert mean("sharp", "sharp") > mean("blur", "sharp") + 0.5
    assert mean("dark", "luma") < mean("sharp", "luma") - 0.15
    assert mean("black", "p95") < ss.black_luma
    assert mean("moving", "motion") > 5 * max(1e-4, mean("still", "motion"))
    rep = analyse_takes([(k, clips[k]) for k in ("sharp", "blur", "dark", "black")], ss, use_faces=False)
    info = shot_info_from_report(rep)
    assert info["black"].quality == 0.0
    assert info["sharp"].quality > info["blur"].quality
    assert info["sharp"].quality > info["dark"].quality


@needs_ffmpeg
def test_camera_setups_group_takes_that_share_a_background(clips):
    rep = analyse_takes([(k, clips[k]) for k in ("setupA", "setupA2", "setupB")], ShotSettings(), use_faces=False)
    assert rep.setups["setupA"] == rep.setups["setupA2"]
    assert rep.setups["setupB"] != rep.setups["setupA"]


@needs_ffmpeg
@pytest.mark.skipif(not HAS_CV2, reason="OpenCV not installed")
def test_faces_give_shot_sizes(clips):
    assert model_ok(), "YuNet model file missing or changed"
    ss = ShotSettings()
    got = {k: analyse_take(k, clips[k], ss) for k in ("close", "medium", "wide", "sharp")}
    assert got["close"].face_detector == "yunet"
    assert all(s.faces >= 1 for s in got["close"].samples)
    assert got["close"].size == "close"
    assert got["medium"].size == "medium"
    assert got["wide"].size == "wide"
    assert all(s.faces == 0 for s in got["sharp"].samples), "no faces in a test pattern"
    fx = [s.face_x for s in got["close"].samples]
    assert 0.3 < sum(fx) / len(fx) < 0.7, "face centred in the close-up"


@needs_ffmpeg
def test_cache_returns_the_same_report(clips, tmp_path):
    items = [("sharp", clips["sharp"])]
    a = analyse_takes(items, ShotSettings(), cache_dir=str(tmp_path), use_faces=False)
    assert os.listdir(tmp_path / "shots")
    b = analyse_takes(items, ShotSettings(), cache_dir=str(tmp_path), use_faces=False)
    assert a.model_dump_json() == b.model_dump_json()


def test_unreadable_clip_is_reported_not_raised(tmp_path):
    bad = tmp_path / "broken.mov"
    bad.write_bytes(b"not a video")
    t = analyse_take("x", str(bad), ShotSettings(), use_faces=False)
    assert t.error and t.samples == []
    assert group_setups({"x": t}, ShotSettings()) == {}
