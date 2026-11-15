"""Ad first cut pipeline shared by the CLI and the helper (engine/ad.py does the matching)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Optional

from .ad import AdPlan, AdSettings, Clip, build_ad_plan, read_takelog, validate_ad_plan
from .analyze import clip_identity
from .probe import ProbeError, probe
from .script import Script, parse_script

Progress = Optional[Callable[[str, int, int, str], None]]  # phase, done, total, current


class AdCutError(ValueError):
    pass


@dataclass
class AdCut:
    script: Script
    clips: list[Clip]
    plan: AdPlan
    warnings: list[str] = field(default_factory=list)
    music_s: Optional[float] = None
    vo_s: Optional[float] = None
    transcribed: int = 0


def probe_clips(paths: list[str], progress: Progress = None) -> tuple[list[Clip], list[str]]:
    clips, warnings = [], []
    for i, p in enumerate(paths, start=1):
        cid, ap = clip_identity(p)
        try:
            mi = probe(ap)
            clips.append(Clip(cid, ap, os.path.basename(ap), mi.duration_s, mi.has_audio, mi.has_video))
        except ProbeError as e:
            warnings.append(f"{os.path.basename(p)}: not readable ({e})")
        if progress:
            progress("reading clips", i, len(paths), os.path.basename(p))
    return clips, warnings


def run_ad_cut(script_path: str, clip_paths: list[str], fps: Fraction, out_dir: str, *,
               takelog_path: Optional[str] = None, music_path: Optional[str] = None, vo_path: Optional[str] = None,
               settings: Optional[AdSettings] = None, transcribe: bool = True, model: str = "base.en",
               models_dir: Optional[str] = None, cache_dir: Optional[str] = None, analyse_shots: bool = False,
               progress: Progress = None) -> AdCut:
    os.makedirs(out_dir, exist_ok=True)
    script = parse_script(script_path, board_dir=os.path.join(out_dir, "boards"))
    clips, warnings = probe_clips(clip_paths, progress)
    if not clips:
        raise AdCutError("no readable clips")
    clips = [c for c in clips if c.has_video]
    log = read_takelog(takelog_path) if takelog_path else None

    transcripts, vo = {}, None
    n_tr = 0
    needs_speech = any(s.kind == "dialogue" for s in script.shots) or (vo_path and any(s.kind == "vo" for s in script.shots))
    if transcribe and needs_speech:
        from .stt import SttError, Transcriber, available, transcripts_for
        if not available():
            warnings.append("speech-to-text is not installed: dialogue is matched by take log and file names only")
        else:
            mdir = models_dir or os.path.join(cache_dir or out_dir, "models")
            try:
                if progress:
                    progress("loading the speech model", 0, 1, model)
                with_audio = [(c.clip_id, c.path) for c in clips if c.has_audio]
                if any(s.kind == "dialogue" for s in script.shots) and with_audio:
                    transcripts = transcripts_for(with_audio, model, mdir, cache_dir,
                                                  progress=(lambda d, n, p: progress("transcribing dialogue", d, n, os.path.basename(p))) if progress else None)
                    n_tr = sum(1 for t in transcripts.values() if t.error is None)
                    for t in transcripts.values():
                        if t.error:
                            warnings.append(f"transcription: {t.error}")
                if vo_path:
                    vo = Transcriber(model, mdir).transcribe_file("vo", vo_path)
                    if vo.error:
                        warnings.append(f"VO recording: {vo.error}")
                        vo = None
            except SttError as e:
                warnings.append(f"dialogue matching off: {e}")

    quality = None
    if analyse_shots:
        from .shots import analyse_takes, shot_info_from_report
        rep = analyse_takes([(c.clip_id, c.path) for c in clips], cache_dir=cache_dir,
                            progress=(lambda d, n, p: progress("analysing shots", d, n, os.path.basename(p))) if progress else None)
        quality = shot_info_from_report(rep)

    beats, music_s = None, None
    if music_path:
        from .music import music_map_for_file
        from .settings import EngineSettings
        if progress:
            progress("reading the music bed", 0, 1, os.path.basename(music_path))
        mm = music_map_for_file(music_path, EngineSettings(), cache_dir)
        beats, music_s = mm.beats, mm.duration_s
    vo_s = None
    if vo_path:
        try:
            vo_s = probe(vo_path).duration_s
        except ProbeError as e:
            warnings.append(f"VO recording not readable: {e}")
            vo_path = None
    if progress:
        progress("matching takes to the script", 0, 1, "")
    plan = build_ad_plan(script, clips, fps, settings or AdSettings(), transcripts=transcripts, takelog=log, vo=vo,
                         vo_path=vo_path, music_path=music_path, beats=beats, quality=quality)
    plan.warnings = warnings + plan.warnings
    problems = validate_ad_plan(plan, clips)
    if problems:
        raise AdCutError("the ad cut breaks a rule: " + "; ".join(problems[:5]))
    return AdCut(script=script, clips=clips, plan=plan, warnings=plan.warnings, music_s=music_s, vo_s=vo_s,
                 transcribed=n_tr)
