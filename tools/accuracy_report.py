"""Accuracy report: compare sync_result.json with truth.csv (spec section 10).

  python tools/accuracy_report.py --truth synthetic/truth.csv --result sync_result.json \
      --out docs/reports/synthetic.md --gate synthetic

Accuracy is proven against known answers, never by eye. Exit code 1 when --gate is given and
a gate metric fails.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.result import SyncResult, load_result  # noqa: E402
from engine.timebase import TICKS_PER_SECOND  # noqa: E402

NEAR_AMBIGUOUS_UNIQUE_FRACTION = 0.3  # less than 30% unique audio: treated as near-ambiguous


def _f(x: str) -> Optional[float]:
    return float(x) if x not in ("", None) else None


def _timecode_to_s(tc: str, fps: Fraction) -> float:
    hh, mm, ss, ff = (int(x) for x in tc.replace(";", ":").split(":"))
    base = round(float(fps))
    frames = ((hh * 60 + mm) * 60 + ss) * base + ff
    return float(Fraction(frames) / fps)


def normalise_truth(rows: list[dict], fps: Fraction) -> list[dict]:
    """Accept the synthetic truth.csv or a minimal real-footage truth file with just
    name + true_position_s or true_timecode (hh:mm:ss:ff on the sequence), and optionally
    expected_reason (e.g. NO_AUDIO for B-roll) and rate. See docs/real_truth_template.csv."""
    out = []
    for r in rows:
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        if not r.get("true_position_s") and r.get("true_timecode"):
            r["true_position_s"] = f"{_timecode_to_s(r['true_timecode'], fps):.6f}"
        r.setdefault("case", "real")
        r.setdefault("expected_reason", "")
        r.setdefault("true_position_s", "")
        r.setdefault("ambiguous_positions", "")
        r["unique_fraction"] = r.get("unique_fraction") or "1.0"
        r["rate"] = r.get("rate") or "1.0"
        r["drift_ppm"] = r.get("drift_ppm") or "0"
        out.append(r)
    return out


def evaluate(truth_rows: list[dict], result: SyncResult) -> dict:
    fps = Fraction(result.sequence.fps_num, result.sequence.fps_den)
    frame = float(1 / fps)
    by_name = {c.name: c for c in result.clips}
    rows = []
    truth_rows = normalise_truth(truth_rows, fps)
    for t in truth_rows:
        c = by_name.get(t["name"])
        row = {"name": t["name"], "case": t["case"], "expected_reason": t["expected_reason"] or None,
               "missing": c is None}
        if c is None:
            rows.append(row)
            continue
        truth = _f(t["true_position_s"])
        amb = [float(x) for x in t["ambiguous_positions"].split(";") if x]
        uf = float(t["unique_fraction"] or 1.0)
        if truth is None:
            kind = "no_truth"
        elif amb or uf == 0.0:
            kind = "ambiguous"
        elif uf < NEAR_AMBIGUOUS_UNIQUE_FRACTION:
            kind = "near_ambiguous"
        else:
            kind = "unique"
        row.update({"status": c.status.value, "reason": c.reason.value if c.reason else None,
                    "kind": kind, "truth": truth, "rate_truth": float(t["rate"]), "rate": c.rate,
                    "score": c.confidence.score, "peak_ratio": c.confidence.peak_ratio,
                    "window_agreement": c.confidence.window_agreement,
                    "drift_truth_ppm": float(t["drift_ppm"] or 0), "drift": c.drift.model_dump() if c.drift else None})
        if truth is not None and c.song_position_ticks is not None:
            r = c.rate or 1.0
            placed = int(c.song_position_ticks) / TICKS_PER_SECOND
            expected = truth + c.source_in_s * r
            err = placed - expected
            row["error_s"] = err
            row["within_1_frame"] = abs(err) <= frame + 1e-9
            cands = [placed] + [a.song_position_s + (c.source_in_s * a.rate if a.song_position_s < 0 else 0) for a in c.alternates]
            truths = [truth] + amb
            row["truth_in_candidates"] = any(abs(x - y) <= frame for x in cands for y in truths)
        rows.append(row)

    def pct(a: int, b: int) -> Optional[float]:
        return round(100.0 * a / b, 2) if b else None

    sync_rows = [r for r in rows if not r.get("missing") and r["expected_reason"] is None and r["kind"] == "unique"]
    within = [r for r in sync_rows if r.get("within_1_frame")]
    placed_ok = [r for r in sync_rows if r.get("within_1_frame") and r["status"] in ("HIGH", "MEDIUM")]
    wrong_hm = [r for r in rows if not r.get("missing") and r.get("status") in ("HIGH", "MEDIUM")
                and r.get("truth") is not None and not r.get("within_1_frame")]
    wrong_hm += [r for r in rows if not r.get("missing") and r.get("status") in ("HIGH", "MEDIUM") and r.get("truth") is None]
    should_not_place = [r for r in rows if not r.get("missing") and r["expected_reason"] in ("NO_MATCH", "NO_AUDIO", "DECODE_ERROR")]
    placed_bad = [r for r in should_not_place if r["status"] != "UNSYNCED"]
    reason_ok = [r for r in rows if not r.get("missing") and r["expected_reason"] and r.get("reason") == r["expected_reason"]]
    expected_reason_rows = [r for r in rows if not r.get("missing") and r["expected_reason"]]
    amb_rows = [r for r in rows if not r.get("missing") and r.get("kind") == "ambiguous" and r["expected_reason"] is None]
    amb_ok = [r for r in amb_rows if r.get("reason") == "AMBIGUOUS" or (r.get("truth_in_candidates") and r["status"] not in ("HIGH", "MEDIUM"))
              or r.get("within_1_frame")]
    amb_flagged = [r for r in amb_rows if r.get("reason") == "AMBIGUOUS"]
    near = [r for r in rows if not r.get("missing") and r.get("kind") == "near_ambiguous"]
    slow = [r for r in rows if not r.get("missing") and r["rate_truth"] != 1.0 and r["expected_reason"] is None]
    slow = [r for r in slow if r.get("rate") is not None]  # clips the engine positioned
    slow_rate_ok = [r for r in slow if r.get("rate") == r["rate_truth"]]
    missing = [r for r in rows if r.get("missing")]

    per_case: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r.get("missing"):
            per_case[r["case"]]["MISSING"] += 1
            continue
        per_case[r["case"]][r["status"]] += 1
        if r.get("within_1_frame"):
            per_case[r["case"]]["within_1f"] += 1
    errors_ms = sorted(abs(r["error_s"]) * 1000 for r in sync_rows if "error_s" in r and r.get("within_1_frame"))
    drift_rows = [{"name": r["name"], "truth_ppm": r["drift_truth_ppm"], "measured": r["drift"]}
                  for r in rows if not r.get("missing") and r["drift_truth_ppm"]]

    metrics = {
        "clips_in_truth": len(truth_rows),
        "clips_in_result": len(result.clips),
        "missing_from_result": [r["name"] for r in missing],
        "frame_s": frame,
        "unique_expected_to_sync": len(sync_rows),
        "position_within_1_frame_pct": pct(len(within), len(sync_rows)),
        "placed_HIGH_MEDIUM_within_1_frame_pct": pct(len(placed_ok), len(sync_rows)),
        "wrong_HIGH_or_MEDIUM": [r["name"] for r in wrong_hm],
        "unrelated_or_no_audio_placed": [r["name"] for r in placed_bad],
        "expected_reason_correct_pct": pct(len(reason_ok), len(expected_reason_rows)),
        "ambiguous_truth_clips": len(amb_rows),
        "ambiguous_flagged_or_correct_pct": pct(len(amb_ok), len(amb_rows)),
        "ambiguous_flagged_AMBIGUOUS_pct": pct(len(amb_flagged), len(amb_rows)),
        "near_ambiguous_clips": len(near),
        "near_ambiguous_wrong_HIGH_MEDIUM": [r["name"] for r in near if r["status"] in ("HIGH", "MEDIUM") and not r.get("within_1_frame")],
        "rate_search_correct_pct": pct(len(slow_rate_ok), len(slow)),
        "abs_error_ms_p50": round(errors_ms[len(errors_ms) // 2], 3) if errors_ms else None,
        "abs_error_ms_p95": round(errors_ms[int(len(errors_ms) * 0.95) - 1], 3) if len(errors_ms) > 1 else None,
        "abs_error_ms_max": round(errors_ms[-1], 3) if errors_ms else None,
        "status_counts": dict(Counter(r["status"] for r in rows if not r.get("missing"))),
        "per_case": {k: dict(v) for k, v in sorted(per_case.items())},
        "drift": drift_rows,
        "misses": [{k: r.get(k) for k in ("name", "case", "status", "reason", "kind", "score", "peak_ratio",
                                           "window_agreement", "error_s")}
                   for r in sync_rows if not r.get("within_1_frame")],
    }
    return {"metrics": metrics, "rows": rows}


GATES = {
    "synthetic": {"position_within_1_frame_pct": 98.0},
    "real": {"position_within_1_frame_pct": 90.0},
}


def check_gate(metrics: dict, gate: str) -> list[str]:
    fails = []
    need = GATES[gate]["position_within_1_frame_pct"]
    got = metrics["position_within_1_frame_pct"] or 0.0
    if got < need:
        fails.append(f"position within 1 frame {got}% < {need}%")
    if metrics["wrong_HIGH_or_MEDIUM"]:
        fails.append(f"wrong HIGH/MEDIUM: {metrics['wrong_HIGH_or_MEDIUM']}")
    if metrics["unrelated_or_no_audio_placed"]:
        fails.append(f"unrelated/no-audio placed: {metrics['unrelated_or_no_audio_placed']}")
    if metrics["ambiguous_truth_clips"] and (metrics["ambiguous_flagged_or_correct_pct"] or 0) < 100.0:
        fails.append(f"chorus-only clips flagged or correct {metrics['ambiguous_flagged_or_correct_pct']}% < 100%")
    if metrics["missing_from_result"]:
        fails.append(f"clips missing from result: {metrics['missing_from_result']}")
    return fails


def to_markdown(m: dict, title: str, extra: Optional[dict] = None) -> str:
    lines = [f"# {title}", ""]
    if extra:
        for k, v in extra.items():
            lines.append(f"- **{k}**: {v}")
        lines.append("")
    lines += ["| Metric | Value |", "|---|---|"]
    for k in ("clips_in_truth", "clips_in_result", "unique_expected_to_sync", "position_within_1_frame_pct",
              "placed_HIGH_MEDIUM_within_1_frame_pct", "wrong_HIGH_or_MEDIUM", "unrelated_or_no_audio_placed",
              "expected_reason_correct_pct", "ambiguous_truth_clips", "ambiguous_flagged_or_correct_pct",
              "ambiguous_flagged_AMBIGUOUS_pct", "near_ambiguous_clips", "near_ambiguous_wrong_HIGH_MEDIUM",
              "rate_search_correct_pct", "abs_error_ms_p50", "abs_error_ms_p95", "abs_error_ms_max",
              "status_counts"):
        lines.append(f"| {k} | {m[k]} |")
    lines += ["", "## Per case", "", "| case | counts |", "|---|---|"]
    for k, v in m["per_case"].items():
        lines.append(f"| {k} | {v} |")
    if m["drift"]:
        lines += ["", "## Drift (reported, never corrected)", "", "| clip | truth ppm | measured |", "|---|---|---|"]
        for d in m["drift"]:
            lines.append(f"| {d['name']} | {d['truth_ppm']} | {d['measured']} |")
    if m["misses"]:
        lines += ["", "## Unique clips not within 1 frame", "", "| clip | case | status | reason | score | ratio | windows | error s |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in m["misses"]:
            lines.append("| " + " | ".join(str(r.get(k)) for k in ("name", "case", "status", "reason", "score",
                                                                      "peak_ratio", "window_agreement", "error_s")) + " |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--truth", required=True)
    p.add_argument("--result", required=True)
    p.add_argument("--out", help="markdown report path")
    p.add_argument("--json", help="json metrics path")
    p.add_argument("--gate", choices=list(GATES))
    p.add_argument("--title", default="Accuracy report")
    p.add_argument("--seconds", type=float, help="analysis wall time to include")
    a = p.parse_args(argv)
    with open(a.truth, newline="", encoding="utf-8") as f:
        truth = list(csv.DictReader(f))
    result = load_result(a.result)
    ev = evaluate(truth, result)
    m = ev["metrics"]
    extra = {"engine_version": result.engine_version, "sequence fps": f"{result.sequence.fps_num}/{result.sequence.fps_den}"}
    if a.seconds:
        extra["analysis wall time (s)"] = a.seconds
    fails = check_gate(m, a.gate) if a.gate else []
    if a.gate:
        extra["gate"] = f"{a.gate}: " + ("PASS" if not fails else "FAIL - " + "; ".join(fails))
    md = to_markdown(m, a.title, extra)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(md)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(ev, f, indent=2, default=str)
    print(md)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
