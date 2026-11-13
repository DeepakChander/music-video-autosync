"""Window validation and drift (spec section 5, steps 8 and 9). Drift is reported, never corrected."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .locate import Candidate, MasterRateView, coarse_candidates, refine
from .settings import EngineSettings


@dataclass
class WindowReport:
    evaluated: int = 0
    agreed: int = 0
    agreement: Optional[float] = None
    drift_ppm: Optional[float] = None
    drift_max_error_ms: Optional[float] = None
    drift_windows: int = 0
    drift_residual_ms: Optional[float] = None
    errors_s: list[float] = field(default_factory=list)


def n_windows(song_len_s: float, s: EngineSettings) -> int:
    if song_len_s < 12.0:
        n = 3
    elif song_len_s < 20.0:
        n = 4
    else:
        n = 5
    return int(min(s.max_windows, max(s.min_windows, n)))


def validate_windows(best: Candidate, clip_feats: np.ndarray, clip_audio: np.ndarray,
                     master_view: MasterRateView, master_audio: np.ndarray, master_dur_s: float,
                     s: EngineSettings, tolerance_s: float) -> WindowReport:
    """Split the (rate-stretched) clip into 3-5 windows and locate each one independently.

    A window agrees when one of its own near-tied candidates (score within the ambiguity ratio of
    the window's best, and above the noise floor) lands within `tolerance_s` of where the
    full-clip position says it should be. Windows that fall outside the song at the full-clip
    position (pre-roll, tail after the song ends) are not evaluated.
    """
    rep = WindowReport()
    hop, sr = s.hop_length, s.analysis_sr
    frames_per_s = sr / hop
    t = clip_feats.shape[0]
    song_len_s = t / frames_per_s
    if song_len_s < s.window_min_clip_s:
        return rep
    n = n_windows(song_len_s, s)
    p0 = best.best_position_s
    coarse_tol = int(np.ceil(tolerance_s * frames_per_s)) + 2
    centres, errs = [], []
    for i in range(n):
        f0, f1 = (i * t) // n, ((i + 1) * t) // n
        w_start_s = f0 / frames_per_s
        expected = p0 + w_start_s
        if expected < 0 or expected + (f1 - f0) / frames_per_s > master_dur_s:
            continue
        rep.evaluated += 1
        cands = coarse_candidates(clip_feats[f0:f1], master_view, s, full_overlap=True)
        if not cands:
            continue
        w_best = cands[0].score
        if w_best < s.noise_floor:
            continue
        expected_lag = int(round(expected * frames_per_s))
        near = [c for c in cands if c.score >= w_best / s.ambiguity_ratio
                and abs(c.lag - expected_lag) <= coarse_tol]
        if not near:
            continue
        c = near[0]
        # The window's candidate is expressed for the window's first frame; re-express it for
        # clip sample 0 so the fine pass can use the full clip buffer restricted to the window.
        seg = (f0 * hop, min(clip_audio.size, f1 * hop + s.n_fft))
        cw = Candidate(rate=c.rate, lag=c.lag - f0, score=c.score, position_s=c.position_s - w_start_s)
        refine(cw, master_audio, clip_audio, s, seg=seg)
        err = cw.best_position_s - p0
        if abs(err) <= tolerance_s:
            rep.agreed += 1
            if cw.fine_used:
                centres.append(w_start_s + (f1 - f0) / (2 * frames_per_s))
                errs.append(err)
    if rep.evaluated:
        rep.agreement = rep.agreed / rep.evaluated
    if len(errs) >= 2:
        x = np.asarray(centres)
        y = np.asarray(errs)
        coef = np.polyfit(x, y, 1)
        slope = float(coef[0])
        rep.drift_residual_ms = float(np.sqrt(np.mean((y - np.polyval(coef, x)) ** 2)) * 1000.0)
        # Song seconds per clip second = 1 - ppm for a camera clock running ppm fast, so the
        # offset error falls by ppm per second. Report ppm > 0 = camera audio clock runs fast.
        rep.drift_ppm = -slope * 1e6
        rep.drift_max_error_ms = float(np.max(np.abs(y)) * 1000.0)
        rep.drift_windows = len(errs)
        rep.errors_s = [float(e) for e in errs]
    return rep
