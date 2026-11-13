"""Golden test: a small fixed synthetic set must keep producing the same statuses and positions.

PCM audio only (no lossy encoders), so the media is identical across FFmpeg versions.
Regenerate after an intentional engine change with:  UPDATE_GOLDEN=1 pytest tests/golden
"""

import json
import os

import pytest

from engine.analyze import analyze, list_media
from engine.result import SequenceInfo
from engine.settings import EngineSettings
from tests.conftest import needs_ffmpeg
from tools.make_synthetic_takes import build

GOLDEN = os.path.join(os.path.dirname(__file__), "golden_seed5.json")
CASES = ["clean", "room", "preroll", "chorus", "slowmo", "noaudio", "unrelated", "short", "avoffset", "silent"]


@needs_ffmpeg
def test_golden(tmp_path):
    build(str(tmp_path), count=2 * len(CASES), seed=5, master_path=None, video_codec="mpeg4",
          container="mov", case_filter=CASES)
    with open(tmp_path / "master_path.txt", encoding="utf-8") as f:
        master = f.read().strip()
    res = analyze(master, list_media(str(tmp_path / "takes")), SequenceInfo(fps_num=25, fps_den=1),
                  EngineSettings(), cache_dir=None, workers=2)
    got = {c.name: {"status": c.status.value, "reason": c.reason.value if c.reason else None,
                    "song_position_s": c.song_position_s, "rate": c.rate, "video_track": c.video_track}
           for c in res.clips}
    if os.environ.get("UPDATE_GOLDEN") or not os.path.exists(GOLDEN):
        with open(GOLDEN, "w", encoding="utf-8") as f:
            json.dump(got, f, indent=2, sort_keys=True)
        if not os.environ.get("UPDATE_GOLDEN"):
            pytest.skip("golden file created")
    with open(GOLDEN, encoding="utf-8") as f:
        want = json.load(f)
    assert set(got) == set(want)
    for name, w in want.items():
        g = got[name]
        assert g["status"] == w["status"], (name, g, w)
        assert g["reason"] == w["reason"], (name, g, w)
        assert g["rate"] == w["rate"], name
        if w["song_position_s"] is None:
            assert g["song_position_s"] is None, name
        else:
            assert abs(g["song_position_s"] - w["song_position_s"]) < 0.001, (name, g, w)
