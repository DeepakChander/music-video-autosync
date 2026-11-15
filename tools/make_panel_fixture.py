"""Build panel/test/fixtures from a real engine run (dev tool).

  python tools/make_panel_fixture.py --set <synthetic dir>

Paths are rewritten to Windows-style D:/shoot/... so the panel tests exercise Windows paths;
the XML is written by the real exporter from the rewritten result.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.analyze import analyze, list_media  # noqa: E402
from engine.result import SequenceInfo  # noqa: E402
from engine.settings import EngineSettings  # noqa: E402
from export.fcp7_xml import XmlOptions, write_xml  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "panel", "test", "fixtures")


def write_cut_fixture() -> str:
    """cut.xml for the panel's first-cut tests: the fixture result cut on a regular 120 BPM grid
    (sections every 8 bars), written by the real planner and exporter."""
    from engine.edit import EditSettings, build_plan, validate_plan
    from engine.music import MusicMap, SongSection
    from engine.result import load_result
    from export.edit_xml import write_edit_xml

    res = load_result(os.path.join(OUT, "sync_result.json"))
    dur = res.master.duration_s
    beats = [round(i * 0.5, 4) for i in range(int(dur / 0.5))]
    downs = beats[::4]
    secs, kinds = [], ["intro", "verse", "chorus", "verse", "chorus", "outro"]
    starts = downs[::8]
    for i, st in enumerate(starts):
        en = starts[i + 1] if i + 1 < len(starts) else dur
        secs.append(SongSection(index=i, start_s=st, end_s=en, bars=8, label="ABCB"[min(i, 3)] if i < 4 else "C",
                                kind=kinds[min(i, len(kinds) - 1)], energy=0.8 if "chorus" in kinds[min(i, len(kinds) - 1)] else 0.5))
    mm = MusicMap(duration_s=dur, tempo_bpm=120.0, tempo_confidence=1.0, beats_per_bar=4, beats=beats,
                  downbeats=downs, beat_energy=[0.6] * len(beats), sections=secs)
    plan = build_plan(res, mm, EditSettings(style="hype"))
    assert validate_plan(plan, res, mm) == []
    out = os.path.join(OUT, "cut.xml")
    write_edit_xml(res, plan, out, "AutoSync Cut - Song Master - fixture")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--set")
    p.add_argument("--cut-only", action="store_true", help="only rewrite cut.xml from the existing sync_result.json")
    a = p.parse_args(argv)
    if a.cut_only:
        print(write_cut_fixture())
        return 0
    if not a.set:
        p.error("--set is required unless --cut-only")
    with open(os.path.join(a.set, "master_path.txt"), encoding="utf-8") as f:
        master = f.read().strip()
    seq = SequenceInfo(fps_num=24000, fps_den=1001, width=1920, height=1080)
    res = analyze(master, list_media(os.path.join(a.set, "takes")), seq, EngineSettings(), cache_dir=None, workers=4)
    res.created_at = "2026-11-13T00:00:00+05:30"
    res.master.path = "D:/shoot/Song Master.wav"
    for c in res.clips:
        c.path = f"D:/shoot/takes/{os.path.basename(c.path)}"
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "sync_result.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(res.to_json())
    write_xml(res, os.path.join(OUT, "draft.xml"), XmlOptions(sequence_name="AutoSync - Song Master - fixture"))
    print(res.summary)
    print(write_cut_fixture())
    return 0


if __name__ == "__main__":
    sys.exit(main())
