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


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--set", required=True)
    a = p.parse_args(argv)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
