"""autosync-engine command line.

  autosync-engine analyze --master song.wav --clips ./footage --fps 24000/1001 --out sync_result.json
  autosync-engine schema --out schema/sync_result.schema.json
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import time

from . import __version__
from .analyze import MasterError, analyze, list_media, write_result
from .probe import FFmpegNotFound, find_tool
from .result import SequenceInfo, json_schema
from .settings import load_settings
from .timebase import parse_rate


def default_cache_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "MusicVideoAutoSync", "cache")


def _cmd_analyze(a: argparse.Namespace) -> int:
    fps = parse_rate(a.fps)
    overrides = {}
    if a.rates:
        overrides["rates"] = tuple(float(x) for x in a.rates.split(","))
    s = load_settings(a.settings, overrides)
    if a.clips_list:
        with open(a.clips_list, "r", encoding="utf-8") as f:
            clips = [ln.strip() for ln in f if ln.strip()]
    else:
        clips = []
        for c in a.clips:
            clips.extend(list_media(c) if os.path.isdir(c) else [c])
    master_abs = os.path.abspath(a.master)
    clips = [c for c in clips if os.path.abspath(c) != master_abs]
    if not clips:
        print("error: no clips found", file=sys.stderr)
        return 2
    seq = SequenceInfo(fps_num=fps.numerator, fps_den=fps.denominator, width=a.width, height=a.height)
    t0 = time.time()

    def progress(ev: dict) -> None:
        if a.quiet or ev["event"] != "clip":
            return
        print(f"[{ev['done']:>4}/{ev['total']}] {ev['current']}  "
              f"H={ev['HIGH']} M={ev['MEDIUM']} L={ev['LOW']} U={ev['UNSYNCED']}", file=sys.stderr, flush=True)

    cache = None if a.no_cache else (a.cache or default_cache_dir())
    try:
        result = analyze(a.master, clips, seq, s, cache_dir=cache, workers=a.workers, progress=progress)
    except (MasterError, FFmpegNotFound) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    write_result(result, a.out)
    dt = time.time() - t0
    sm = result.summary
    print(json.dumps({"out": os.path.abspath(a.out), "seconds": round(dt, 2), "clips": sm.total,
                      "HIGH": sm.HIGH, "MEDIUM": sm.MEDIUM, "LOW": sm.LOW, "UNSYNCED": sm.UNSYNCED,
                      "video_tracks": sm.video_tracks}))
    return 0


def _cmd_schema(a: argparse.Namespace) -> int:
    text = json.dumps(json_schema(), indent=2) + "\n"
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


def _cmd_report(a: argparse.Namespace) -> int:
    """Readable list of every clip: status, reason, song position (timecode), track, evidence."""
    from fractions import Fraction

    from .result import load_result
    from .timebase import TICKS_PER_SECOND

    res = load_result(a.result)
    fps = Fraction(res.sequence.fps_num, res.sequence.fps_den)
    base = round(float(fps))

    def tc(ticks):
        if ticks is None:
            return "-"
        frames = int(round(Fraction(int(ticks), TICKS_PER_SECOND) * fps))
        s, f = divmod(frames, base)
        return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"

    order = {"UNSYNCED": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    rows = sorted(res.clips, key=lambda c: (order[c.status.value], c.name))
    print(f"{'clip':32} {'status':9} {'position':12} {'track':5} {'score':6} {'ratio':6}  reason / note")
    for c in rows:
        pos = c.override.song_position_ticks if c.override else c.song_position_ticks
        track = c.override.video_track if c.override else c.video_track
        note = (c.reason.value if c.reason else "") + (f": {c.reason_detail}" if c.reason_detail else "")
        if c.rate and c.rate != 1:
            note += f" playback {c.rate:g}x"
        if c.alternates and c.status.value == "LOW":
            note += " alternates " + ", ".join(tc(int(round(max(0.0, x.song_position_s) * TICKS_PER_SECOND))) for x in c.alternates[:3])
        print(f"{c.name[:32]:32} {c.status.value:9} {tc(pos):12} {('V%d' % track) if track else '-':5} "
              f"{c.confidence.score:<6.3f} {c.confidence.peak_ratio:<6.2f}  {note.strip()}")
    sm = res.summary
    print(f"\n{sm.total} clips: {sm.HIGH} HIGH, {sm.MEDIUM} MEDIUM, {sm.LOW} LOW, {sm.UNSYNCED} UNSYNCED; "
          f"{sm.video_tracks} video tracks for HIGH/MEDIUM")
    return 0


def _cmd_doctor(_a: argparse.Namespace) -> int:
    out = {"engine_version": __version__}
    for tool in ("ffmpeg", "ffprobe"):
        try:
            out[tool] = find_tool(tool)
        except FFmpegNotFound as e:
            out[tool] = None
            out["error"] = str(e)
    print(json.dumps(out, indent=2))
    return 0 if out.get("ffmpeg") and out.get("ffprobe") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autosync-engine", description="Music Video AutoSync engine")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    an = sub.add_parser("analyze", help="locate every clip in the master song")
    an.add_argument("--master", required=True)
    an.add_argument("--clips", nargs="*", default=[], help="clip files and/or folders")
    an.add_argument("--clips-list", help="text file with one clip path per line")
    an.add_argument("--fps", required=True, help="sequence frame rate, e.g. 24000/1001 or 25")
    an.add_argument("--width", type=int, default=3840)
    an.add_argument("--height", type=int, default=2160)
    an.add_argument("--out", required=True)
    an.add_argument("--settings", help="JSON file overriding engine settings")
    an.add_argument("--rates", help="comma-separated playback rates, e.g. 1,2")
    an.add_argument("--workers", type=int)
    an.add_argument("--cache")
    an.add_argument("--no-cache", action="store_true")
    an.add_argument("--quiet", action="store_true")
    an.set_defaults(func=_cmd_analyze)

    sc = sub.add_parser("schema", help="write the JSON Schema of sync_result.json")
    sc.add_argument("--out")
    sc.set_defaults(func=_cmd_schema)

    rp = sub.add_parser("report", help="print every clip's status, position and reason")
    rp.add_argument("--result", required=True)
    rp.set_defaults(func=_cmd_report)

    dr = sub.add_parser("doctor", help="check that FFmpeg is available")
    dr.set_defaults(func=_cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
