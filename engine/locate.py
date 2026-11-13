"""Coarse search (FFT normalised cross-correlation of features), rate search, fine alignment
(GCC-PHAT on the waveform with parabolic interpolation), alternates.

Positions are "song seconds of the clip's first decoded audio sample" in this module.
analyze.py converts them to the song position of the clip's first video frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.fft import irfft, next_fast_len, rfft
from scipy.ndimage import maximum_filter1d

from .settings import EngineSettings


@dataclass
class Candidate:
    rate: float
    lag: int                      # feature frames; song frame index of clip frame 0
    score: float                  # normalised feature correlation at `lag`
    position_s: float             # coarse song position (lag * hop / sr)
    fine_position_s: Optional[float] = None
    fine_peak: Optional[float] = None
    fine_used: bool = False

    @property
    def best_position_s(self) -> float:
        return self.fine_position_s if self.fine_used and self.fine_position_s is not None else self.position_s


@dataclass
class MasterRateView:
    rate: float
    feats: np.ndarray             # (Tm, D)
    energy_cumsum: np.ndarray     # (Tm + 1,)
    _fft_cache: dict = field(default_factory=dict, repr=False)

    @classmethod
    def build(cls, rate: float, feats: np.ndarray) -> "MasterRateView":
        e = np.concatenate([[0.0], np.cumsum((feats.astype(np.float64) ** 2).sum(axis=1))])
        return cls(rate=rate, feats=feats, energy_cumsum=e)

    def fft(self, n: int) -> np.ndarray:
        f = self._fft_cache.get(n)
        if f is None:
            f = rfft(self.feats, n, axis=0)
            if len(self._fft_cache) > 8:
                self._fft_cache.clear()
            self._fft_cache[n] = f
        return f


def ncc_all_lags(clip: np.ndarray, master: MasterRateView, min_overlap: int) -> tuple[np.ndarray, np.ndarray]:
    """Normalised correlation of clip features against every lag of the master.

    lag l means clip frame t aligns with master frame t + l. Only lags with an overlap of at
    least `min_overlap` frames are returned. Score = <C, M> / (|C| |M|) over the overlap.
    """
    tc, d = clip.shape
    tm = master.feats.shape[0]
    min_overlap = int(max(1, min(min_overlap, tc, tm)))
    n = next_fast_len(tc + tm - 1)
    cross = irfft((np.conj(rfft(clip, n, axis=0)) * master.fft(n)).sum(axis=1), n)
    lags = np.arange(-(tc - min_overlap), tm - min_overlap + 1)
    num = cross[np.mod(lags, n)]
    ec = np.concatenate([[0.0], np.cumsum((clip.astype(np.float64) ** 2).sum(axis=1))])
    t_lo = np.maximum(0, -lags)
    t_hi = np.minimum(tc, tm - lags)
    e_clip = ec[t_hi] - ec[t_lo]
    e_mast = master.energy_cumsum[t_hi + lags] - master.energy_cumsum[t_lo + lags]
    score = num / np.sqrt(np.maximum(e_clip * e_mast, 1e-12))
    return lags, score.astype(np.float64)


def top_peaks(lags: np.ndarray, score: np.ndarray, k: int, sep: int) -> list[tuple[int, float]]:
    """Top-k local maxima with non-maximum suppression (at least `sep` frames apart)."""
    if score.size == 0:
        return []
    sep = max(1, int(sep))
    local_max = maximum_filter1d(score, size=2 * sep + 1, mode="nearest")
    idx = np.flatnonzero(score >= local_max)
    order = idx[np.lexsort((lags[idx], -score[idx]))]  # by score desc, then lag asc (deterministic)
    picked: list[int] = []
    for i in order:
        if all(abs(int(lags[i]) - int(lags[p])) >= sep for p in picked):
            picked.append(int(i))
            if len(picked) >= k:
                break
    return [(int(lags[i]), float(score[i])) for i in picked]


def min_overlap_frames(n_clip_frames: int, s: EngineSettings) -> int:
    frames_per_s = s.analysis_sr / s.hop_length
    by_time = int(math.ceil(s.min_overlap_s * frames_per_s))
    by_frac = int(math.ceil(s.min_overlap_fraction * n_clip_frames))
    return max(1, min(n_clip_frames, max(by_time, by_frac)))


def coarse_candidates(clip_feats: np.ndarray, master: MasterRateView, s: EngineSettings,
                      k: Optional[int] = None, full_overlap: bool = False) -> list[Candidate]:
    frames_per_s = s.analysis_sr / s.hop_length
    mo = clip_feats.shape[0] if full_overlap else min_overlap_frames(clip_feats.shape[0], s)
    lags, score = ncc_all_lags(clip_feats, master, mo)
    sep = int(round(s.nms_separation_s * frames_per_s))
    peaks = top_peaks(lags, score, k or s.coarse_top_k, sep)
    return [Candidate(rate=master.rate, lag=l, score=sc, position_s=l / frames_per_s) for l, sc in peaks]


def gcc_phat_refine(master_audio: np.ndarray, clip_audio: np.ndarray, coarse_pos_s: float,
                    rate: float, s: EngineSettings, seg: Optional[tuple[int, int]] = None
                    ) -> tuple[Optional[float], float]:
    """Refine a coarse position at sample level.

    Returns (position_s, peak) where position_s is the song time of clip sample 0 and peak is the
    normalised GCC-PHAT peak (0..1). position_s is None when there is no usable overlap.
    `seg` restricts the clip samples used (start, end).
    """
    sr = s.analysis_sr
    pos = int(round(coarse_pos_s * sr))
    a, b = seg if seg is not None else (0, clip_audio.size)
    # keep only the part of the clip that overlaps the song at this position
    a = max(a, -pos)
    b = min(b, master_audio.size - pos)
    if b - a < int(0.5 * sr):
        return None, 0.0
    max_len = int(s.fine_max_segment_s * sr)
    if b - a > max_len:
        mid = (a + b) // 2
        a, b = mid - max_len // 2, mid + max_len // 2
    search = int(round(s.fine_search_s * sr))
    m0 = pos + a - search
    m1 = pos + b + search
    mseg = np.zeros(m1 - m0, dtype=np.float32)
    src0, src1 = max(0, m0), min(master_audio.size, m1)
    if src1 <= src0:
        return None, 0.0
    mseg[src0 - m0: src1 - m0] = master_audio[src0:src1]
    cseg = clip_audio[a:b].astype(np.float32)
    n = next_fast_len(mseg.size + cseg.size)
    x = rfft(mseg, n)
    y = rfft(cseg, n)
    r = x * np.conj(y)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    hi = min(s.fmax_hz, 0.95 * sr / (2.0 * rate))
    band = (freqs >= s.fmin_hz) & (freqs <= hi)
    mag = np.abs(r)
    r = np.where(band & (mag > 1e-12), r / np.maximum(mag, 1e-12), 0.0)
    cc = irfft(r, n)
    norm = 2.0 * float(band.sum()) / n  # value of a perfect (coherent) peak
    window = cc[: 2 * search + 1]
    k = int(np.argmax(window))
    peak = float(window[k] / max(norm, 1e-12))
    shift = float(k)
    if 0 < k < window.size - 1:
        y0, y1, y2 = window[k - 1], window[k], window[k + 1]
        denom = y0 - 2.0 * y1 + y2
        if abs(denom) > 1e-12:
            shift = k + 0.5 * (y0 - y2) / denom
    # mseg[0] is master sample m0; clip sample a aligns with master sample m0 + shift
    position = float((m0 + shift - a) / sr)
    return position, peak


def refine(c: Candidate, master_audio: np.ndarray, clip_audio: np.ndarray, s: EngineSettings,
           seg: Optional[tuple[int, int]] = None) -> Candidate:
    pos, peak = gcc_phat_refine(master_audio, clip_audio, c.position_s, c.rate, s, seg)
    c.fine_peak = peak
    c.fine_position_s = pos
    c.fine_used = (pos is not None and peak >= s.fine_min_peak
                   and abs(pos - c.position_s) <= s.fine_max_shift_s)
    return c


def peak_ratio(best: Candidate, others: list[Candidate], s: EngineSettings) -> tuple[float, list[Candidate]]:
    """best score / best score of a *distinct* candidate (>1 s away, or a different rate)."""
    distinct = [o for o in others if o is not best and (
        abs(o.rate - best.rate) > 1e-9 or abs(o.best_position_s - best.best_position_s) > s.nms_separation_s)]
    if not distinct:
        return 99.0, []
    second = max(o.score for o in distinct)
    if second <= 1e-6:
        return 99.0, distinct
    return min(99.0, best.score / second), distinct
