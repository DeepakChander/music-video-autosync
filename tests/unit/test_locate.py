import numpy as np

from engine.locate import Candidate, MasterRateView, gcc_phat_refine, ncc_all_lags, peak_ratio, top_peaks
from engine.settings import EngineSettings

S = EngineSettings()


def test_ncc_finds_planted_lag_including_negative():
    rng = np.random.default_rng(0)
    master = rng.standard_normal((3000, 8)).astype(np.float32)
    view = MasterRateView.build(1.0, master)
    clip = master[1200:1500] + 0.3 * rng.standard_normal((300, 8)).astype(np.float32)
    lags, sc = ncc_all_lags(clip, view, 100)
    assert lags[int(np.argmax(sc))] == 1200
    # pre-roll: 50 frames of noise, then the master from frame 0
    pre = np.concatenate([rng.standard_normal((50, 8)), master[:250]]).astype(np.float32)
    lags, sc = ncc_all_lags(pre, view, 100)
    assert lags[int(np.argmax(sc))] == -50


def test_top_peaks_respects_separation():
    lags = np.arange(100)
    sc = np.zeros(100)
    sc[[10, 12, 50]] = [1.0, 0.9, 0.5]
    peaks = top_peaks(lags, sc, k=3, sep=5)
    assert [p[0] for p in peaks][:2] == [10, 50]


def test_gcc_phat_subsample_accuracy():
    sr = S.analysis_sr
    rng = np.random.default_rng(1)
    master = rng.standard_normal(sr * 20).astype(np.float32)
    true_pos = 5.0 + 0.4 / sr  # fractional sample offset
    # build the clip by fractional delay in the frequency domain
    n = master.size
    spec = np.fft.rfft(master)
    f = np.fft.rfftfreq(n)
    shifted = np.fft.irfft(spec * np.exp(2j * np.pi * f * (true_pos * sr)), n).astype(np.float32)
    clip = shifted[: sr * 6]
    pos, peak = gcc_phat_refine(master, clip, 5.01, 1.0, S)
    assert abs(pos - true_pos) < 0.2 / sr
    assert peak > 0.5


def test_peak_ratio_ignores_same_position_and_counts_other_rates():
    best = Candidate(1.0, 100, 0.8, 1.0)
    near = Candidate(1.0, 150, 0.79, 1.5)       # 0.5 s away: not distinct
    far = Candidate(1.0, 400, 0.4, 4.0)
    other_rate = Candidate(2.0, 100, 0.5, 1.0)  # same position, other rate: distinct
    ratio, distinct = peak_ratio(best, [best, near, far, other_rate], S)
    assert ratio == 0.8 / 0.5
    assert near not in distinct and far in distinct and other_rate in distinct
