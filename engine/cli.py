"""autosync-engine command line.

  autosync-engine analyze --master song.wav --clips ./footage --fps 24000/1001 --out sync_result.json
  autosync-engine schema --out schema/sync_result.schema.json
  autosync-engine music --master song.wav
  autosync-engine cut --result sync_result.json --style performance --out cut.xml --preview cut.mp4
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
        detail = " / ".join(x.strip() for x in (c.reason_detail or "").splitlines() if x.strip())
        note = (c.reason.value if c.reason else "") + (f": {detail}" if detail else "")
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


def _cmd_music(a: argparse.Namespace) -> int:
    from .music import music_map_for_file
    mm = music_map_for_file(a.master, load_settings(None, {}), None if a.no_cache else (a.cache or default_cache_dir()))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(mm.model_dump_json(indent=2))
    print(f"tempo {mm.tempo_bpm:.2f} BPM, {len(mm.beats)} beats, {len(mm.downbeats)} bars")
    for sec in mm.sections:
        print(f"  {sec.start_s:8.2f}s  {sec.kind:11s} {sec.label}  {sec.bars:3d} bars  energy {sec.energy:.2f}")
    return 0


def _video_clips(res) -> list[tuple[str, str]]:
    return [(c.clip_id, c.path) for c in res.clips if c.source_fps]


def _cmd_shots(a: argparse.Namespace) -> int:
    from .result import load_result
    from .shots import analyse_takes
    res = load_result(a.result)
    rep = analyse_takes(_video_clips(res), cache_dir=None if a.no_cache else (a.cache or default_cache_dir()),
                        workers=a.workers or max(1, (os.cpu_count() or 2) // 2), use_faces=not a.no_faces,
                        progress=None if a.quiet else (lambda d, n, p: print(f"[{d}/{n}] {os.path.basename(p)}", file=sys.stderr)))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(rep.model_dump_json())
    names = {c.clip_id: c.name for c in res.clips}
    for cid, t in sorted(rep.takes.items(), key=lambda kv: names.get(kv[0], kv[0])):
        faces = sum(1 for s in t.samples if s.faces) / max(1, len(t.samples))
        print(f"{names.get(cid, cid)[:32]:32} {t.size or '-':7} setup {rep.setups.get(cid, '-'):4} faces {faces:4.0%} "
              f"{'error: ' + t.error if t.error else ''}")
    return 0


def _cmd_cut(a: argparse.Namespace) -> int:
    from export.edit_xml import cut_sequence_name, write_edit_xml

    from .edit import EditSettings
    from .firstcut import FirstCutError, make_first_cut
    from .music import music_map_for_file
    from .result import load_result
    from .shots import ShotsReport, analyse_takes

    res = load_result(a.result)
    by_name = {c.name: c.clip_id for c in res.clips}
    ids = lambda names: [by_name.get(n, n) for n in (names or [])]  # noqa: E731
    settings = EditSettings(style=a.style, pace=a.pace, seed=a.seed, effects=not a.no_effects,
                            story_share=a.story_share, early_frames=a.early_frames, place_low=a.place_low,
                            story_clip_ids=ids(a.story), exclude_clip_ids=ids(a.exclude))
    cache = None if a.no_cache else (a.cache or default_cache_dir())
    mm = music_map_for_file(res.master.path, load_settings(None, {}), cache)
    shots = None
    if a.shots:
        with open(a.shots, encoding="utf-8") as f:
            shots = ShotsReport.model_validate_json(f.read())
    elif a.analyse_shots or a.colour:
        shots = analyse_takes(_video_clips(res), cache_dir=cache, workers=a.workers or max(1, (os.cpu_count() or 2) // 2))
    out_dir = os.path.dirname(os.path.abspath(a.out or a.preview or a.plan or a.result))
    try:
        fc = make_first_cut(res, mm, settings, shots, out_dir, colour_match=a.colour, look=a.look,
                            hero_clip_id=ids([a.hero])[0] if a.hero else None)
    except FirstCutError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    plan = fc.plan
    if fc.colour:
        print(f"colour: every take matched to {fc.colour.hero_name}; LUTs in {fc.colour.folder}")
    elif fc.look_lut:
        print(f"look: {fc.look_lut}")
    if a.plan:
        with open(a.plan, "w", encoding="utf-8") as f:
            f.write(plan.model_dump_json(indent=2))
    name = a.name or cut_sequence_name(res.master.path, a.style)
    st = plan.stats
    print(f"{st['shots']} shots ({st['performance_shots']} performance, {st['story_shots']} story), "
          f"mean {st['mean_shot_s']} s, {st['takes_used']} takes used; tempo {mm.tempo_bpm:.1f} BPM")
    for w in plan.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if a.out:
        info = write_edit_xml(res, plan, a.out, name, fc.luts)
        print(f"Wrote {info['path']}: sequence '{name}' (File > Import in Premiere)")
    if a.preview:
        from export.preview import render_preview
        info = render_preview(res, plan, a.preview, width=a.preview_width, workers=a.workers or 4, luts=fc.luts,
                              progress=None if a.quiet else (lambda d, n: print(f"\rrendering {d}/{n}", end="", file=sys.stderr)))
        if not a.quiet:
            print(file=sys.stderr)
        print(f"Wrote {info['path']} ({info['width']}x{info['height']}, {info['encoder']})")
    return 0


def _cmd_ad(a: argparse.Namespace) -> int:
    from export.ad_xml import ad_sequence_name, write_ad_xml

    from .ad import AdSettings
    from .adcut import AdCutError, run_ad_cut
    from .script import ScriptError, write_template

    if a.template:
        print(write_template(a.template))
        return 0
    if not a.script or not a.clips:
        print("error: --script and --clips are required (or --template FILE)", file=sys.stderr)
        return 2
    clips = []
    for c in a.clips:
        clips.extend(list_media(c) if os.path.isdir(c) else [c])
    fps = parse_rate(a.fps)
    out_dir = os.path.dirname(os.path.abspath(a.out or a.plan or a.preview or a.script))
    cache = None if a.no_cache else (a.cache or default_cache_dir())
    try:
        cut = run_ad_cut(a.script, clips, fps, out_dir, takelog_path=a.takelog, music_path=a.music, vo_path=a.vo,
                         settings=AdSettings(target_s=a.target, language=a.language), transcribe=not a.no_transcribe, model=a.model,
                         cache_dir=cache, analyse_shots=a.analyse_shots,
                         progress=None if a.quiet else (lambda ph, d, n, cur: print(f"{ph} {d}/{n} {cur}", file=sys.stderr)),
                         cutdowns=a.cutdown or [])
    except (AdCutError, ScriptError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    plan = cut.plan
    st = plan.stats
    print(f"{st['matched']}/{st['shots']} shots matched, {st['suggested']} suggested, missing: "
          f"{', '.join(st['missing']) or 'none'}; {st['seconds']} s" + (f" (target {st['target_s']:g} s)" if st['target_s'] else ""))
    for s in plan.shots:
        print(f"  {s.shot_id:6} {s.kind:9} {s.status:9} {(s.name or '-')[:28]:28} {s.method:18} {s.confidence:6} {s.reason}")
    for w in plan.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if a.plan:
        with open(a.plan, "w", encoding="utf-8") as f:
            f.write(plan.model_dump_json(indent=2))
    name = a.name or ad_sequence_name(a.script, a.target)
    if a.out:
        info = write_ad_xml(plan, cut.clips, a.out, name, a.width, a.height, cut.music_s, cut.vo_s)
        print(f"Wrote {info['path']}: sequence '{name}' (File > Import in Premiere)")
    if a.preview:
        from export.preview import render_ad_preview
        info = render_ad_preview(plan, {c.clip_id: c.path for c in cut.clips}, a.preview, width=a.preview_width,
                                 height=int(round(a.preview_width * a.height / a.width / 2)) * 2)
        print(f"Wrote {info['path']} ({info['width']}x{info['height']}, {info['encoder']})")
    for v in cut.versions:
        vname = ad_sequence_name(a.script, v["length"])
        print(f"{v['length']:g} s version ({v['how']}): {v['plan'].stats['matched']} shots, dropped {', '.join(v['dropped']) or 'none'}")
        if a.out:
            vout = os.path.splitext(a.out)[0] + f"_{v['length']:g}s.xml"
            write_ad_xml(v["plan"], cut.clips, vout, vname, a.width, a.height, cut.music_s, cut.vo_s)
            print(f"  Wrote {vout}")
    return 0


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

    mu = sub.add_parser("music", help="tempo, beats, bars and sections of the master song")
    mu.add_argument("--master", required=True)
    mu.add_argument("--out", help="write the music map JSON here")
    mu.add_argument("--cache")
    mu.add_argument("--no-cache", action="store_true")
    mu.set_defaults(func=_cmd_music)

    sh = sub.add_parser("shots", help="analyse the pictures: faces and shot size, sharpness, exposure, setups, colour")
    sh.add_argument("--result", required=True)
    sh.add_argument("--out", required=True)
    sh.add_argument("--no-faces", action="store_true")
    sh.add_argument("--workers", type=int)
    sh.add_argument("--cache")
    sh.add_argument("--no-cache", action="store_true")
    sh.add_argument("--quiet", action="store_true")
    sh.set_defaults(func=_cmd_shots)

    cu = sub.add_parser("cut", help="build a first cut (Premiere XML and/or preview MP4) from a sync result")
    cu.add_argument("--result", required=True, help="sync_result.json from analyze")
    cu.add_argument("--style", default="performance", choices=["performance", "performance_story", "hype", "ballad"])
    cu.add_argument("--pace", type=float, default=1.0, help=">1 = more cuts, <1 = longer shots")
    cu.add_argument("--seed", type=int, default=0, help="another number gives another variation")
    cu.add_argument("--story-share", type=float, help="share of the song for story footage (0-0.8)")
    cu.add_argument("--story", nargs="*", help="clip names to treat as story footage")
    cu.add_argument("--exclude", nargs="*", help="clip names to leave out")
    cu.add_argument("--early-frames", type=int, default=0, help="cut this many frames before the beat")
    cu.add_argument("--place-low", action="store_true", help="also use LOW clips")
    cu.add_argument("--no-effects", action="store_true")
    cu.add_argument("--shots", help="shot analysis JSON from the shots command")
    cu.add_argument("--analyse-shots", action="store_true", help="analyse the pictures now (faces, sharpness, setups)")
    cu.add_argument("--colour", action="store_true", help="match every take's colour to a hero take (writes .cube LUTs)")
    cu.add_argument("--look", default="none", choices=["none", "warm", "cool", "teal_orange", "bw_contrast", "film_fade"])
    cu.add_argument("--hero", help="clip name of the colour reference take (default: chosen automatically)")
    cu.add_argument("--out", help="the .xml file to write (File > Import in Premiere)")
    cu.add_argument("--name", help="sequence name")
    cu.add_argument("--plan", help="write the edit plan JSON here")
    cu.add_argument("--preview", help="render a preview .mp4 here")
    cu.add_argument("--preview-width", type=int, default=1280)
    cu.add_argument("--workers", type=int)
    cu.add_argument("--cache")
    cu.add_argument("--no-cache", action="store_true")
    cu.add_argument("--quiet", action="store_true")
    cu.set_defaults(func=_cmd_cut)

    ad = sub.add_parser("ad", help="ad first cut from a script / storyboard / shot list")
    ad.add_argument("--script", help=".csv/.xlsx shot list, .docx AV script, .fdx, .txt or storyboard .pdf")
    ad.add_argument("--clips", nargs="*", default=[], help="footage files and/or folders")
    ad.add_argument("--fps", default="25", help="sequence frame rate, e.g. 25 or 24000/1001")
    ad.add_argument("--width", type=int, default=1920)
    ad.add_argument("--height", type=int, default=1080)
    ad.add_argument("--target", type=float, help="spot length in seconds (15, 30, 60...)")
    ad.add_argument("--takelog", help="take log CSV: clip, shot, take, circled, notes")
    ad.add_argument("--music", help="music bed (cuts of picture-only shots snap to its beats)")
    ad.add_argument("--cutdown", type=float, nargs="*", help="shorter versions too, e.g. --cutdown 15 6")
    ad.add_argument("--vo", help="VO recording (VO shots last as long as their line)")
    ad.add_argument("--language", default="en", help="dialogue language: en, auto, or a code such as hi, es, fr, de")
    ad.add_argument("--model", choices=["tiny.en", "base.en", "small.en", "tiny", "base", "small"],
                    help="speech model (default: base.en for English, base for other languages)")
    ad.add_argument("--no-transcribe", action="store_true", help="match by take log / file names only")
    ad.add_argument("--analyse-shots", action="store_true", help="pick the best-looking part of picture-only takes")
    ad.add_argument("--out", help="the .xml file to write (File > Import in Premiere)")
    ad.add_argument("--name")
    ad.add_argument("--plan", help="write the ad plan JSON here")
    ad.add_argument("--preview", help="render a preview .mp4 here")
    ad.add_argument("--preview-width", type=int, default=1280)
    ad.add_argument("--template", help="write the shot-list template CSV here and stop")
    ad.add_argument("--cache")
    ad.add_argument("--no-cache", action="store_true")
    ad.add_argument("--quiet", action="store_true")
    ad.set_defaults(func=_cmd_ad)

    dr = sub.add_parser("doctor", help="check that FFmpeg is available")
    dr.set_defaults(func=_cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
