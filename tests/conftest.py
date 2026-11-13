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
