"""Synthetic end-to-end: generate takes with known truth, analyse, check the spec's gates."""

import os
import threading

import pytest

from engine.analyze import analyze, list_media, result_without_timestamps
from engine.result import Reason, SequenceInfo, Status
from engine.settings import EngineSettings
from tests.conftest import needs_ffmpeg
from tools.accuracy_report import check_gate, evaluate

SEQ = SequenceInfo(fps_num=24000, fps_den=1001, width=1920, height=1080)


@pytest.fixture(scope="module")
def synth(synth_set):
    return synth_set["dir"], synth_set["truth"]


def _master(out):
    with open(os.path.join(out, "master_path.txt"), encoding="utf-8") as f:
        return f.read().strip()


@needs_ffmpeg
def test_gate_metrics_and_every_clip_reported(synth, tmp_path):
    out, truth = synth
    clips = list_media(os.path.join(out, "takes"))
    res = analyze(_master(out), clips, SEQ, EngineSettings(), cache_dir=str(tmp_path / "c"), workers=4)
    assert len(res.clips) == len(truth) == len(clips)
    m = evaluate(truth, res)["metrics"]
    assert check_gate(m, "synthetic") == [], m
    # every expected failure reason is reported with the right reason
    assert m["expected_reason_correct_pct"] == 100.0
    for c in res.clips:
        if c.status == Status.UNSYNCED:
            assert c.song_position_s is None and c.reason is not None and c.video_track is None
        else:
            assert c.video_track is not None and c.video_track >= 1


@needs_ffmpeg
def test_deterministic_output_independent_of_workers(synth, tmp_path):
    out, _ = synth
    clips = list_media(os.path.join(out, "takes"))
    a = analyze(_master(out), clips, SEQ, EngineSettings(), cache_dir=None, workers=1)
    b = analyze(_master(out), list(reversed(clips)), SEQ, EngineSettings(), cache_dir=None, workers=3)
    assert result_without_timestamps(a) == result_without_timestamps(b)


@needs_ffmpeg
def test_cancel_keeps_finished_and_reports_the_rest(synth, tmp_path):
    out, _ = synth
    clips = list_media(os.path.join(out, "takes"))
    cancel = threading.Event()

    def progress(ev):
        if ev["event"] == "clip" and ev["done"] >= 3:
            cancel.set()

    res = analyze(_master(out), clips, SEQ, EngineSettings(), cache_dir=str(tmp_path / "c"), workers=1,
                  progress=progress, cancel=cancel)
    assert len(res.clips) == len(clips)
    not_analysed = [c for c in res.clips if c.reason == Reason.NOT_ANALYZED]
    assert len(not_analysed) == len(clips) - 3


@needs_ffmpeg
def test_rerun_resumes_from_cache(synth, tmp_path):
    out, _ = synth
    clips = list_media(os.path.join(out, "takes"))
    cache = str(tmp_path / "c")
    first = analyze(_master(out), clips[:5], SEQ, EngineSettings(), cache_dir=cache, workers=2)
    events = []
    second = analyze(_master(out), clips, SEQ, EngineSettings(), cache_dir=cache, workers=2, progress=events.append)
    assert events[0]["cache_hits"] == 5
    by_id = {c.clip_id: c for c in second.clips}
    for c in first.clips:
        assert by_id[c.clip_id].song_position_ticks == c.song_position_ticks
