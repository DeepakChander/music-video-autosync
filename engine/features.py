"""Hand-written log-mel / MFCC / onset features (numpy + scipy only; no librosa/numba).

Frame t covers samples [t*hop, t*hop + n_fft). Master and clip use the same convention,
so a lag of L frames is exactly L*hop samples.
"""

from __future__ import annotations

from fractions import Fraction
from functools import lru_cache

import numpy as np
from scipy.fft import dct, rfft
from scipy.signal import resample_poly

from .settings import EngineSettings

_EPS = 1e-10


def _hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


def _mel_to_hz(m):
    return 700.0 * (10.0 ** (np.asarray(m, dtype=np.float64) / 2595.0) - 1.0)


@lru_cache(maxsize=16)
def mel_filterbank(sr: int, n_fft: int, n_mels: int, fmin: float, fmax: float) -> np.ndarray:
    mels = np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2)
    hz = _mel_to_hz(mels)
    bins = np.fft.rfftfreq(n_fft, 1.0 / sr)
    fb = np.zeros((n_mels, bins.size), dtype=np.float64)
    for i in range(n_mels):
        lo, c, hi = hz[i], hz[i + 1], hz[i + 2]
        up = (bins - lo) / max(c - lo, _EPS)
        down = (hi - bins) / max(hi - c, _EPS)
        fb[i] = np.maximum(0.0, np.minimum(up, down))
    # Area-normalise so bands of different width have comparable energy.
    fb /= np.maximum(fb.sum(axis=1, keepdims=True), _EPS)
    return fb.astype(np.float32)


@lru_cache(maxsize=4)
def _window(win_length: int, n_fft: int) -> np.ndarray:
    w = np.hanning(win_length + 2)[1:-1].astype(np.float32)  # periodic-ish, no zero ends
    pad = n_fft - win_length
    return np.pad(w, (pad // 2, pad - pad // 2))


def n_frames(n_samples: int, s: EngineSettings) -> int:
    if n_samples < s.n_fft:
        return 1
    return 1 + (n_samples - s.n_fft) // s.hop_length


def log_mel(x: np.ndarray, s: EngineSettings, fmax: float) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if x.size < s.n_fft:
        x = np.pad(x, (0, s.n_fft - x.size))
    frames = np.lib.stride_tricks.sliding_window_view(x, s.n_fft)[:: s.hop_length]
    frames = frames[: n_frames(x.size, s)]
    spec = rfft(frames * _window(s.win_length, s.n_fft), axis=1)
    power = (spec.real ** 2 + spec.imag ** 2).astype(np.float32)
    fb = mel_filterbank(s.analysis_sr, s.n_fft, s.n_mels, s.fmin_hz, float(fmax))
    return np.log(power @ fb.T + _EPS)


def standardise(f: np.ndarray) -> np.ndarray:
    mu = f.mean(axis=0, keepdims=True)
    sd = f.std(axis=0, keepdims=True)
    return ((f - mu) / np.maximum(sd, 1e-6)).astype(np.float32)


def features(x: np.ndarray, s: EngineSettings, rate: float = 1.0) -> np.ndarray:
    """(T, D) standardised MFCC c0..c(n_mfcc-1) plus a weighted onset-strength column.

    `x` must already be stretched by `rate` (see stretch()). The mel range is capped at the
    content limit of a rate-r match so empty high bands do not dilute the correlation.
    """
    lm = log_mel(x, s, s.feature_fmax_for_rate(rate))
    mf = dct(lm, type=2, norm="ortho", axis=1)[:, : s.n_mfcc]
    flux = np.maximum(0.0, np.diff(lm, axis=0, prepend=lm[:1])).sum(axis=1, keepdims=True)
    out = np.concatenate([standardise(mf), standardise(flux) * np.float32(np.sqrt(s.onset_weight))], axis=1)
    return out.astype(np.float32)


def rate_fraction(rate: float) -> Fraction:
    return Fraction(rate).limit_denominator(100)


def stretch(x: np.ndarray, rate: float) -> np.ndarray:
    """Undo a playback speed-up: a clip recorded against rate-r playback is resampled so it
    lasts r times longer. This restores the song's tempo and pitch (spec section 5, step 5)."""
    if abs(rate - 1.0) < 1e-9:
        return np.asarray(x, dtype=np.float32)
    fr = rate_fraction(rate)
    return resample_poly(x, fr.numerator, fr.denominator).astype(np.float32)
