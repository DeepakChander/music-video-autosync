"""Accuracy of the music map on synthetic songs whose beats, bars and sections are known exactly.

    python tools/music_eval.py                # default grid of tempos, seeds and layouts
    python tools/music_eval.py --json out.json

Metrics (MIREX-style tolerances):
  tempo      relative error of the BPM; "octave" when the BPM is half or double the truth
  beat F     F-measure of beats within 70 ms of a true beat
  downbeat   fraction of detected downbeats within 70 ms of a true bar line
  bound P/R  section boundaries within 2 beats (and within 3 s, the MIREX tolerance); boundaries
             between two literally identical consecutive sections are not required (inaudible)
  chorus     fraction of true chorus time labelled "chorus"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.music import analyse_music  # noqa: E402
from engine.settings import EngineSettings  # noqa: E402
from tools.synth_song import make_song  # noqa: E402

TOL = 0.07


def f_measure(est: np.ndarray, ref: np.ndarray, tol: float = TOL) -> float:
    if est.size == 0 or ref.size == 0:
        return 0.0
    used = np.zeros(ref.size, dtype=bool)
    hit = 0
    for t in est:
        d = np.abs(ref - t)
        d[used] = np.inf
        j = int(np.argmin(d))
        if d[j] <= tol:
            used[j] = True
            hit += 1
    p, r = hit / est.size, hit / ref.size
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def evaluate(bpm: float, seed: int, layout: str, s: EngineSettings) -> dict:
    song = make_song(seed=seed, bpm=bpm, layout=layout)
    audio = resample_poly(song.audio, 1, 3).astype(np.float32)  # 48 kHz -> 16 kHz
    t0 = time.perf_counter()
    mm = analyse_music(audio, s)
    secs = time.perf_counter() - t0
    beat = round(60.0 / bpm * song.sr) / song.sr
    ref_beats = np.arange(0, song.duration_s - 1e-6, beat)
    ref_bars = ref_beats[::4]
    est_beats = np.array(mm.beats)
    est_down = np.array(mm.downbeats)
    ratio = mm.tempo_bpm / bpm
    octave = min(abs(ratio - 2), abs(ratio - 0.5)) < 0.02
    down_ok = float(np.mean([np.min(np.abs(ref_bars - t)) <= TOL for t in est_down])) if est_down.size else 0.0
    # Required boundaries: every section start except one that repeats the previous section's audio.
    req, opt = [], []
    for prev, sec in zip(song.sections, song.sections[1:]):
        same = (sec.copy_of or sec.name) == (prev.copy_of or prev.name)
        (opt if same else req).append(sec.start_s)
    est_b = [x.start_s for x in mm.sections[1:]]
    tol_b = 2 * beat
    hits_r = sum(any(abs(e - r) <= tol_b for e in est_b) for r in req)
    hits_p = sum(any(abs(e - r) <= tol_b for r in req + opt) for e in est_b)
    hits_r3 = sum(any(abs(e - r) <= 3.0 for e in est_b) for r in req)  # MIREX 3 s tolerance
    hits_p3 = sum(any(abs(e - r) <= 3.0 for r in req + opt) for e in est_b)
    chorus_true = [(x.start_s, x.end_s) for x in song.sections if (x.copy_of or x.name).startswith(("chorus", "b"))]
    def overlap(a, b):
        return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    tot = sum(e - s_ for s_, e in chorus_true)
    lab = sum(overlap(c, (x.start_s, x.end_s)) for c in chorus_true for x in mm.sections if x.kind == "chorus")
    return {
        "bpm": bpm, "seed": seed, "layout": layout, "est_bpm": mm.tempo_bpm,
        "tempo_err_pct": round(abs(ratio - 1) * 100, 3), "octave": bool(octave),
        "beat_f": round(f_measure(est_beats, ref_beats), 4), "downbeat": round(down_ok, 4),
        "bound_recall": round(hits_r / max(1, len(req)), 4), "bound_precision": round(hits_p / max(1, len(est_b)), 4),
        "bound_recall_3s": round(hits_r3 / max(1, len(req)), 4), "bound_precision_3s": round(hits_p3 / max(1, len(est_b)), 4),
        "chorus_labelled": round(lab / tot, 4) if tot else None,
        "sections": [f"{x.kind}:{x.label}:{x.bars}" for x in mm.sections], "seconds": round(secs, 2),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--bpms", default="80,96,112,128,140,170")
    ap.add_argument("--seeds", default="1,2")
    ap.add_argument("--layouts", default="pop,short")
    a = ap.parse_args(argv)
    s = EngineSettings()
    rows = []
    for layout in a.layouts.split(","):
        for bpm in [float(b) for b in a.bpms.split(",")]:
            for seed in [int(x) for x in a.seeds.split(",")]:
                r = evaluate(bpm, seed, layout, s)
                rows.append(r)
                print(f"{layout:5s} {bpm:5.0f} seed{seed}  est {r['est_bpm']:7.2f}  beatF {r['beat_f']:.3f}  "
                      f"down {r['downbeat']:.2f}  boundR {r['bound_recall']:.2f} P {r['bound_precision']:.2f}  "
                      f"chorus {r['chorus_labelled']}  {r['seconds']}s  {' '.join(r['sections'])}")
    keys = ["beat_f", "downbeat", "bound_recall", "bound_precision", "bound_recall_3s", "bound_precision_3s"]
    summary = {k: round(float(np.mean([r[k] for r in rows])), 4) for k in keys}
    summary["tempo_within_1pct"] = round(float(np.mean([r["tempo_err_pct"] <= 1.0 for r in rows])), 4)
    summary["octave_errors"] = int(sum(r["octave"] for r in rows))
    summary["chorus_labelled"] = round(float(np.mean([r["chorus_labelled"] for r in rows if r["chorus_labelled"] is not None])), 4)
    print(json.dumps(summary))
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"summary": summary, "rows": rows}, f, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
