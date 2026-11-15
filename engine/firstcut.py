"""First cut pipeline shared by the CLI and the helper: plan (+ shot analysis) and colour LUTs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .colour import LOOKS, ColourPlan, build_lut, plan_colour, write_cube
from .edit import EditPlan, EditSettings, build_plan, validate_plan
from .music import MusicMap
from .result import SyncResult
from .shots import ShotsReport, shot_info_from_report


class FirstCutError(ValueError):
    pass


@dataclass
class FirstCut:
    plan: EditPlan
    colour: Optional[ColourPlan]
    luts: dict[str, str]           # clip_id -> .cube applied in the preview (match + look)
    look_lut: Optional[str]


def make_first_cut(result: SyncResult, music: MusicMap, settings: EditSettings, shots: Optional[ShotsReport],
                   out_dir: str, colour_match: bool = False, look: str = "none",
                   hero_clip_id: Optional[str] = None) -> FirstCut:
    if look not in LOOKS:
        raise FirstCutError(f"unknown look {look!r}; choose from {', '.join(LOOKS)}")
    info = shot_info_from_report(shots) if shots else None
    plan = build_plan(result, music, settings, info)
    problems = validate_plan(plan, result, music)
    if problems:  # never hand Premiere a cut that breaks a rule
        raise FirstCutError("the cut breaks a rule: " + "; ".join(problems[:5]))
    used = sorted({s.clip_id for s in plan.shots if s.clip_id})
    names = {c.clip_id: c.name for c in result.clips}
    colour = None
    luts: dict[str, str] = {}
    look_lut = None
    folder = os.path.join(out_dir, "luts")
    if colour_match:
        if shots is None:
            raise FirstCutError("Colour match needs the shot analysis: run it first.")
        # Performance takes are matched to the hero; story footage keeps its own palette (it is a
        # different scene on purpose) and only gets the look.
        perf = sorted({s.clip_id for s in plan.shots if s.kind == "performance" and s.clip_id})
        stats = {cid: shots.takes[cid].colour for cid in perf if cid in shots.takes and shots.takes[cid].colour}
        if not stats:
            raise FirstCutError("No performance take in the cut has usable colour statistics (black or unreadable picture).")
        colour = plan_colour(stats, names, folder, look=look, hero=hero_clip_id,
                             exposure={k: v.quality for k, v in (info or {}).items()},
                             sizes={k: shots.takes[k].size for k in stats},
                             face_stats={k: shots.takes[k].face_colour for k in stats})
        luts = {cid: tc.final_lut for cid, tc in colour.takes.items() if tc.final_lut}
        look_lut = colour.look_lut
        if look_lut:
            for cid in used:
                luts.setdefault(cid, look_lut)
    elif look != "none":
        os.makedirs(folder, exist_ok=True)
        look_lut = write_cube(os.path.join(folder, f"look_{look}.cube"), build_lut(None, look), f"AutoSync look {look}")
        luts = {cid: look_lut for cid in used}
    return FirstCut(plan=plan, colour=colour, luts=luts, look_lut=look_lut)
