import os
import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from engine.analyze import analyze, list_media
from engine.overrides import placeable
from engine.result import SequenceInfo
from engine.settings import EngineSettings
from engine.timebase import TICKS_PER_SECOND
from export.fcp7_xml import XmlOptions, build_xml, pathurl, timebase_ntsc
from tests.conftest import needs_ffmpeg


def test_timebase_table():
    assert timebase_ntsc(Fraction(24000, 1001)) == (24, True)
    assert timebase_ntsc(Fraction(30000, 1001)) == (30, True)
    assert timebase_ntsc(Fraction(60000, 1001)) == (60, True)
    assert timebase_ntsc(Fraction(25)) == (25, False)


def test_pathurl_windows_and_posix():
    assert pathurl(r"D:\My Proj\A 1.mov") == "file://localhost/D%3a/My%20Proj/A%201.mov"
    assert pathurl("/Users/x/a#b.mov") == "file://localhost/Users/x/a%23b.mov"


@pytest.fixture(scope="module")
def result(synth_set):
    seq = SequenceInfo(fps_num=24000, fps_den=1001, width=1920, height=1080)
    return analyze(synth_set["master"], list_media(os.path.join(synth_set["dir"], "takes")), seq,
                   EngineSettings(), cache_dir=None, workers=4)


@needs_ffmpeg
def test_xml_structure_matches_result(result):
    xml = build_xml(result, XmlOptions(sequence_name="AutoSync - t"))
    root = ET.fromstring(xml.split("\n", 2)[2])
    assert root.tag == "xmeml" and root.get("version") == "5"
    seq = root.find("sequence")
    tpf = Fraction(TICKS_PER_SECOND) / Fraction(24000, 1001)
    vtracks = seq.findall("media/video/track")
    placed = [c for c in result.clips if placeable(c)]
    starts = {}
    for t_index, tr in enumerate(vtracks, start=1):
        for ci in tr.findall("clipitem"):
            starts[ci.find("name").text] = (t_index, int(ci.find("start").text))
    assert len(starts) == len([c for c in placed if c.source_fps])
    for c in placed:
        if not c.source_fps:
            continue
        track, start = starts[c.name]
        assert track == c.video_track
        assert start == int(Fraction(int(c.song_position_ticks)) / tpf)
    atracks = seq.findall("media/audio/track")
    assert atracks[0].find("clipitem/start").text == "0"            # master on A1 at 0
    assert len(atracks) == len(vtracks) + 1                           # one scratch track per video track
    # no UNSYNCED or LOW clip is in the sequence
    names = set(starts)
    assert not any(c.name in names for c in result.clips if c.status.value in ("UNSYNCED", "LOW"))


@needs_ffmpeg
def test_xml_read_by_independent_parser(result):
    """OpenTimelineIO's FCP7 reader (independent implementation) sees the same start frames."""
    otio = pytest.importorskip("opentimelineio")
    xml = build_xml(result, XmlOptions(sequence_name="AutoSync - t"))
    tl = otio.adapters.read_from_string(xml, adapter_name="fcp_xml")
    fps = 24000 / 1001
    seen = {}
    for track in tl.video_tracks():
        for clip in track.find_clips():
            rng = clip.trimmed_range_in_parent()
            seen[clip.name] = round(rng.start_time.to_frames(fps))
    for c in result.clips:
        if placeable(c) and c.source_fps:
            want = round(int(c.song_position_ticks) / TICKS_PER_SECOND * fps)
            assert seen[c.name] == want, c.name
