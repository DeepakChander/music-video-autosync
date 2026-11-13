"""Threshold calibration (spec section 5: thresholds are calibrated, not invented).

Runs the engine with tiering disabled (every clip gets its best position) on a synthetic set,
labels each clip correct / wrong against truth.csv, and proposes:
  noise_floor     above every unrelated clip's best score, with a margin
  ambiguity_ratio above every wrong-copy pick's peak ratio, with a margin
  HIGH / MEDIUM   so that no wrong position would be rated HIGH or MEDIUM, with a margin

  python tools/calibrate.py --set ./calib --out docs/reports/calibration.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.analyze import analyze, list_media  # noqa: E402
from engine.result import SequenceInfo  # noqa: E402
from engine.settings import EngineSettings, TierThresholds  # noqa: E402
from engine.timebase import TICKS_PER_SECOND  # noqa: E402


def collect(set_dir: str, s: EngineSettings, fps: Fraction, cache: str | None) -> list[dict]:
    with open(os.path.join(set_dir, "master_path.txt"), encoding="utf-8") as f:
        master = f.read().strip()
    with open(os.path.join(set_dir, "truth.csv"), newline="", encoding="utf-8") as f:
        truth = {r["name"]: r for r in csv.DictReader(f)}
    seq = SequenceInfo(fps_num=fps.numerator, fps_den=fps.denominator)
    res = analyze(master, list_media(os.path.join(set_dir, "takes")), seq, s, cache_dir=cache)
    frame = float(1 / fps)
    rows = []
    for c in res.clips:
        t = truth[c.name]
        row = {"name": c.name, "case": t["case"], "expected_reason": t["expected_reason"],
               "status": c.status.value, "reason": c.reason.value if c.reason else None,
               "score": c.confidence.score, "peak_ratio": c.confidence.peak_ratio,
               "window_agreement": c.confidence.window_agreement,
               "fine_used": c.diagnostics.fine_used, "fine_peak": c.diagnostics.fine_peak,
               "unique_fraction": float(t["unique_fraction"] or 1.0), "correct": None}
        if t["true_position_s"] and c.song_position_ticks is not None:
            r = c.rate or 1.0
            err = int(c.song_position_ticks) / TICKS_PER_SECOND - (float(t["true_position_s"]) + c.source_in_s * r)
            row["error_s"] = err
            row["correct"] = abs(err) <= frame and abs(r - float(t["rate"])) < 1e-9
        rows.append(row)
    return rows


def propose(rows: list[dict], margin: float = 0.05) -> dict:
    unrelated = [r["score"] for r in rows if r["expected_reason"] == "NO_MATCH"]
    wrong = [r for r in rows if r["correct"] is False]
    right = [r for r in rows if r["correct"] is True]
    noise_floor = round(max(unrelated, default=0.0) + margin, 2)
    wrong_ratio = [r["peak_ratio"] for r in wrong if r["score"] >= noise_floor]
    ambiguity = round(max([1.10] + [x + margin for x in wrong_ratio]), 2)

    def safe(score_t: float, ratio_t: float, wa_t: float | None) -> bool:
        for r in wrong:
            if r["score"] >= score_t and r["peak_ratio"] >= ratio_t and r["peak_ratio"] >= ambiguity and (
                    wa_t is None or (r["window_agreement"] is not None and r["window_agreement"] >= wa_t)):
                return False
        return True

    return {
        "n_clips": len(rows), "n_right": len(right), "n_wrong": len(wrong),
        "max_unrelated_score": max(unrelated, default=None),
        "wrong_cases": [{k: r[k] for k in ("name", "case", "score", "peak_ratio", "window_agreement", "unique_fraction", "error_s")} for r in wrong],
        "noise_floor": noise_floor, "ambiguity_ratio": ambiguity,
        "right_score_p05": sorted(r["score"] for r in right)[max(0, len(right) // 20 - 1)] if right else None,
        "safe_check": {"high_default": safe(0.5, 1.6, 0.99), "medium_default": safe(0.4, 1.4, 0.66)},
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--set", required=True)
    p.add_argument("--fps", default="24000/1001")
    p.add_argument("--out", required=True)
    p.add_argument("--cache")
    p.add_argument("--min-floor", type=float, default=0.0, help="never propose a floor below this")
    p.add_argument("--min-ambiguity", type=float, default=1.0, help="never propose a ratio below this")
    a = p.parse_args(argv)
    fr = Fraction(a.fps) if "/" not in a.fps else Fraction(*map(int, a.fps.split("/")))
    open_tiers = dict(high=TierThresholds(score=0.0, peak_ratio=0.0, window_agreement=0.0),
                      medium=TierThresholds(score=0.0, peak_ratio=0.0, window_agreement=None))
    # Stage 1: no floor, no ambiguity cut -> calibrate noise_floor and ambiguity_ratio.
    rows1 = collect(a.set, EngineSettings(noise_floor=0.0, ambiguity_ratio=1.0, **open_tiers), fr, a.cache)
    prop = propose(rows1)
    floor = max(prop["noise_floor"], a.min_floor)
    amb = max(prop["ambiguity_ratio"], a.min_ambiguity)
    # Stage 2: floor and ambiguity applied (they also drive window validation) -> look at the
    # evidence of every clip that would be HIGH/MEDIUM with open tier thresholds.
    rows2 = collect(a.set, EngineSettings(noise_floor=floor, ambiguity_ratio=amb, **open_tiers), fr, a.cache)
    placed = [r for r in rows2 if r["status"] in ("HIGH", "MEDIUM")]
    wrong2 = [r for r in placed if r["correct"] is not True]
    right2 = [r for r in placed if r["correct"] is True]

    def pct(vals, q):
        vals = sorted(v for v in vals if v is not None)
        return round(vals[min(len(vals) - 1, int(q * len(vals)))], 4) if vals else None

    stage2 = {
        "noise_floor_used": floor, "ambiguity_ratio_used": amb,
        "placed": len(placed), "placed_wrong": [{k: r.get(k) for k in ("name", "case", "score", "peak_ratio", "window_agreement", "error_s")} for r in wrong2],
        "right_score_p01_p05_p50": [pct([r["score"] for r in right2], q) for q in (0.01, 0.05, 0.5)],
        "right_ratio_p01_p05_p50": [pct([r["peak_ratio"] for r in right2], q) for q in (0.01, 0.05, 0.5)],
        "right_window_agreement_lt_1": sum(1 for r in right2 if r["window_agreement"] is not None and r["window_agreement"] < 1),
        "right_window_agreement_values": sorted(r["window_agreement"] for r in right2 if r["window_agreement"] is not None and r["window_agreement"] < 1),
        "wrong_score_max": max((r["score"] for r in wrong2), default=None),
        "wrong_ratio_max": max((r["peak_ratio"] for r in wrong2), default=None),
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"stage1": prop, "stage2": stage2, "rows_stage1": rows1, "rows_stage2": rows2}, f, indent=2)
    print(json.dumps({"stage1": {k: v for k, v in prop.items() if k != "wrong_cases"}, "stage2": stage2}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
