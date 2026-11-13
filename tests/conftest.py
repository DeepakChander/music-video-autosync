import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _has_ffmpeg() -> bool:
    try:
        from engine.probe import find_tool
        find_tool("ffmpeg")
        find_tool("ffprobe")
        return True
    except Exception:
        return False


HAS_FFMPEG = _has_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg not installed")


@pytest.fixture(scope="session")
def ffmpeg_bin():
    from engine.probe import find_tool
    return find_tool("ffmpeg")


@pytest.fixture(scope="session")
def synth_set(tmp_path_factory):
    """Two takes of every synthetic case (28 clips, MPEG-4 video, known truth)."""
    if not HAS_FFMPEG:
        pytest.skip("FFmpeg not installed")
    import csv
    from tools.make_synthetic_takes import CASES_DEFAULT, build
    out = tmp_path_factory.mktemp("synth")
    build(str(out), count=2 * len(CASES_DEFAULT), seed=11, master_path=None, video_codec="mpeg4", container="mov")
    with open(out / "truth.csv", newline="", encoding="utf-8") as f:
        truth = list(csv.DictReader(f))
    with open(out / "master_path.txt", encoding="utf-8") as f:
        master = f.read().strip()
    return {"dir": str(out), "truth": truth, "master": master}
