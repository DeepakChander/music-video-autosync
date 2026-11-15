"""Music map of the master song: tempo, beats, bars, sections and energy (numpy + scipy only).

The editor cuts on this grid, so every cut lands on a beat and every section change is a cut.

Methods (sources in docs/EDIT_DESIGN.md):
  * Onset envelope: positive log-mel spectral flux, mean over bands (as librosa.onset.onset_strength).
  * Tempo: autocorrelation of the onset envelope weighted by a log-normal prior centred on 120 BPM
    (as librosa.feature.tempo / Ellis 2007).
  * Beats: Ellis (2007) dynamic programming, tightness 100 (librosa's default).
  * Downbeats: 4/4 assumed; the bar phase is the beat phase with the largest harmonic change
    (chords change on bar lines) plus low-band onset (kick drum).
  * Sections: bar-level self-similarity of chroma + timbre + loudness; Foote (2000) checkerboard
    novelty peaks become boundaries; segments are grouped by bar-sequence similarity into labels
    (A, B, C ...), and labels are named (intro, verse, pre-chorus, chorus, bridge, outro) by
    position, repetition and loudness.
Nothing here is guessed silently: confidence values travel with the map and the panel shows them.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from pydantic import BaseModel, ConfigDict
from scipy.fft import dct, rfft
from scipy.ndimage import gaussian_filter1d, maximum_filter1d, uniform_filter1d

from .features import log_mel
from .settings import EngineSettings

MUSIC_MAP_REVISION = 1


class MusicSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # design: onset envelope frame rate = sync engine hop (16 kHz / 160 = 100 frames per second).
    min_bpm: float = 50.0
    max_bpm: float = 220.0
    # librosa.feature.tempo defaults: prior centred on 120 BPM, 1 octave standard deviation.
    prior_bpm: float = 120.0
    prior_octaves: float = 1.0
    # librosa.beat.beat_track default tightness.
    tightness: float = 100.0
    beats_per_bar: int = 4
    # design: section boundaries need at least 4 bars between them (song sections are 4-16 bars).
    min_section_bars: int = 4
    # design: Foote checkerboard kernel half-width in bars.
    novelty_half_bars: int = 4
    # design: bar-sequence similarity above which two segments share a label.
    same_label_similarity: float = 0.90
    # Section method: "laplacian" (McFee & Ellis 2014) or "novelty" (Foote + Serra structure features).
    segmentation: str = "laplacian"
    # Number of section types. calibrated (tools/music_eval.py, 30 synthetic songs): k=4 labelled
    # 75% of chorus time on average; 0 (choose 3..6 per song by segmentation_score) gave 66%.
    laplacian_k: int = 4
    # calibrated (tools/music_eval.py, 30 songs): 1 bar raised 2-beat boundary F from 0.44 to 0.56.
    refine_window_bars: int = 1


class SongSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    start_s: float
    end_s: float
    bars: int
    label: str          # A, B, C ... (same letter = repeated section)
    kind: str           # intro | verse | pre-chorus | chorus | bridge | outro | section
    energy: float       # 0..1, loudness relative to the song


class MusicMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = MUSIC_MAP_REVISION
    duration_s: float
    tempo_bpm: float
    tempo_confidence: float      # 0..1: autocorrelation peak strength
    beats_per_bar: int
    beats: list[float]           # seconds
    downbeats: list[float]       # seconds (subset of beats)
    beat_energy: list[float]     # 0..1 per beat
    sections: list[SongSection]

    def section_at(self, t: float) -> Optional[SongSection]:
        for s in self.sections:
            if s.start_s <= t < s.end_s:
                return s
        return self.sections[-1] if self.sections and t >= self.sections[-1].start_s else None


# --------------------------------------------------------------------------------------
# Envelope, tempo, beats
# --------------------------------------------------------------------------------------

def onset_envelope(audio: np.ndarray, s: EngineSettings) -> tuple[np.ndarray, np.ndarray, float]:
    """(full-band onset strength, low-band onset strength, frames per second)."""
    lm = log_mel(audio, s, s.fmax_hz)  # natural-log mel power, (T, n_mels)
    db = lm * (10.0 / math.log(10.0))
    flux = np.maximum(0.0, np.diff(db, axis=0, prepend=db[:1]))
    full = flux.mean(axis=1)
    low = flux[:, :6].mean(axis=1)  # lowest mel bands: kick drum / bass
    fps = s.analysis_sr / s.hop_length
    return full.astype(np.float64), low.astype(np.float64), fps


def _normalise(env: np.ndarray, fps: float) -> np.ndarray:
    hp = env - uniform_filter1d(env, size=max(3, int(fps)))  # remove the slow loudness trend
    hp = np.maximum(hp, 0.0)
    sd = hp.std()
    return hp / sd if sd > 1e-9 else hp


def estimate_tempo(env: np.ndarray, fps: float, m: MusicSettings) -> tuple[float, float]:
    """(BPM, confidence 0..1) from the global autocorrelation of the onset envelope."""
    x = env - env.mean()
    n = x.size
    if n < 4:
        return m.prior_bpm, 0.0
    spec = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    if ac[0] <= 0:
        return m.prior_bpm, 0.0
    ac = ac / ac[0]
    lo = max(1, int(math.floor(60.0 * fps / m.max_bpm)))
    hi = min(n - 2, int(math.ceil(60.0 * fps / m.min_bpm)))
    if hi <= lo + 2:
        return m.prior_bpm, 0.0
    lags = np.arange(lo, hi + 1)
    bpm = 60.0 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpm / m.prior_bpm) / m.prior_octaves) ** 2)
    score = ac[lags] * prior
    k = int(np.argmax(score))
    lag = float(lags[k])
    if 0 < k < len(lags) - 1:  # parabolic refinement of the lag
        a, b, c = ac[lags[k] - 1], ac[lags[k]], ac[lags[k] + 1]
        den = a - 2 * b + c
        if abs(den) > 1e-12:
            lag += 0.5 * (a - c) / den
    return 60.0 * fps / lag, float(max(0.0, min(1.0, ac[lags[k]])))


def track_beats(env: np.ndarray, fps: float, bpm: float, m: MusicSettings) -> np.ndarray:
    """Ellis (2007) dynamic-programming beat tracker; returns beat frame indices."""
    period = 60.0 * fps / bpm
    local = gaussian_filter1d(env, sigma=max(1.0, period / 32.0))
    n = local.size
    lo, hi = int(round(period / 2)), int(round(2 * period))
    offsets = np.arange(-hi, -lo + 1)  # candidate previous-beat offsets
    txwt = -m.tightness * np.log(-offsets / period) ** 2
    cum = np.zeros(n)
    back = np.full(n, -1, dtype=np.int64)
    thresh = 0.01 * local.max() if local.size else 0.0
    first = True
    for i in range(n):
        idx = i + offsets
        ok = idx >= 0
        if first and local[i] < thresh:
            cum[i] = local[i]
            continue
        first = False
        if not ok.any():
            cum[i] = local[i]
            continue
        cand = txwt[ok] + cum[idx[ok]]
        j = int(np.argmax(cand))
        cum[i] = local[i] + cand[j]
        back[i] = idx[ok][j]
    # Last beat: the last local maximum of the cumulative score that is reasonably strong.
    peaks = np.flatnonzero((cum[1:-1] >= cum[:-2]) & (cum[1:-1] >= cum[2:])) + 1
    if peaks.size == 0:
        return np.array([], dtype=np.int64)
    good = peaks[cum[peaks] >= 0.5 * np.median(cum[peaks])]
    i = int(good[-1]) if good.size else int(peaks[-1])
    beats = []
    while i >= 0:
        beats.append(i)
        i = int(back[i])
    beats = np.array(beats[::-1], dtype=np.int64)
    # Trim weak beats at the start and end (silence or fade).
    if beats.size:
        strength = local[beats]
        floor = 0.5 * np.sqrt(np.mean(strength ** 2))
        keep = np.flatnonzero(strength >= floor)
        if keep.size:
            beats = beats[keep[0]: keep[-1] + 1]
    return beats


def extend_grid(beat_frames: np.ndarray, period: float, audio: np.ndarray, s: EngineSettings,
                silence_dbfs: float = -50.0) -> np.ndarray:
    """Continue the beat grid back to the start and on to the end of the music.

    The tracker drops weak first beats (the first frame has no onset by construction), which
    would start bar counting a bar late. Beats are added at the tracked period while the audio
    around them is above `silence_dbfs`."""
    hop = s.hop_length
    n_frames = audio.size // hop
    def loud(f: float) -> bool:
        a = int(max(0, f) * hop)
        seg = audio[a: a + int(period * hop)]
        return seg.size > 0 and 20 * np.log10(np.sqrt(np.mean(seg.astype(np.float64) ** 2)) + 1e-12) > silence_dbfs
    head = []
    f = beat_frames[0] - period
    while f > -0.25 * period and loud(f):
        head.append(int(round(max(0.0, f))))
        f -= period
    tail = []
    f = beat_frames[-1] + period
    while f < n_frames - 0.25 * period and loud(f):
        tail.append(int(round(f)))
        f += period
    return np.concatenate([np.array(head[::-1], dtype=np.int64), beat_frames, np.array(tail, dtype=np.int64)])


# --------------------------------------------------------------------------------------
# Chroma, bars, downbeats
# --------------------------------------------------------------------------------------

def _chroma_matrix(n_fft: int, sr: int) -> np.ndarray:
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    m = np.zeros((12, freqs.size))
    ok = (freqs >= 55.0) & (freqs <= 4200.0)
    pc = np.mod(np.round(12 * np.log2(freqs[ok] / 440.0)) + 9, 12).astype(int)  # A = 9
    m[pc, np.flatnonzero(ok)] = 1.0
    return m


def interval_features(audio: np.ndarray, sr: int, bounds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Chroma (12) and RMS for each [bounds[i], bounds[i+1]) sample interval."""
    n_fft = 8192
    cm = _chroma_matrix(n_fft, sr)
    chroma = np.zeros((len(bounds) - 1, 12))
    rms = np.zeros(len(bounds) - 1)
    win = np.hanning(n_fft)
    for i in range(len(bounds) - 1):
        a, b = int(bounds[i]), int(bounds[i + 1])
        seg = audio[a:b].astype(np.float64)
        if seg.size == 0:
            continue
        rms[i] = math.sqrt(float(np.mean(seg ** 2)))
        # Average the power spectrum over n_fft windows inside the interval.
        hop = n_fft // 2
        acc = np.zeros(n_fft // 2 + 1)
        count = 0
        for st in range(0, max(1, seg.size - n_fft + 1), hop):
            fr = seg[st: st + n_fft]
            if fr.size < n_fft:
                fr = np.pad(fr, (0, n_fft - fr.size))
            sp = np.abs(rfft(fr * win)) ** 2
            acc += sp
            count += 1
        c = cm @ (acc / max(count, 1))
        chroma[i] = c / (np.linalg.norm(c) + 1e-12)
    return chroma, rms


def choose_downbeat_phase(beat_chroma: np.ndarray, low_onset_at_beats: np.ndarray, bpb: int) -> tuple[int, float]:
    """Beat phase (0..bpb-1) of the downbeats and a confidence (margin between best and second)."""
    nb = beat_chroma.shape[0]
    if nb < 2 * bpb:
        return 0, 0.0
    change = np.zeros(nb)
    change[1:] = 1.0 - np.sum(beat_chroma[1:] * beat_chroma[:-1], axis=1)
    lo = low_onset_at_beats / (low_onset_at_beats.max() + 1e-12)
    ch = change / (change.max() + 1e-12)
    score = np.zeros(bpb)
    for ph in range(bpb):
        sel = np.arange(ph, nb, bpb)
        score[ph] = 2.0 * ch[sel].mean() + lo[sel].mean()
    order = np.argsort(score)[::-1]
    conf = float((score[order[0]] - score[order[1]]) / (abs(score[order[0]]) + 1e-12))
    return int(order[0]), conf


# --------------------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------------------

def _cos_ssm(f: np.ndarray) -> np.ndarray:
    g = f - f.mean(axis=0, keepdims=True)
    g = g / (np.linalg.norm(g, axis=1, keepdims=True) + 1e-12)
    return g @ g.T


def foote_novelty(ssm: np.ndarray, half: int) -> np.ndarray:
    n = ssm.shape[0]
    if n < 2:
        return np.zeros(n)
    k = np.ones((2 * half, 2 * half))
    k[:half, half:] = -1
    k[half:, :half] = -1
    g = np.exp(-0.5 * ((np.arange(2 * half) - half + 0.5) / (half / 1.5)) ** 2)
    k *= np.outer(g, g)
    pad = np.pad(ssm, half, mode="edge")
    nov = np.zeros(n + 1)  # novelty at bar boundary b (between bar b-1 and bar b)
    for b in range(1, n):
        win = pad[b: b + 2 * half, b: b + 2 * half]
        nov[b] = float(np.sum(win * k))
    nov[nov < 0] = 0
    return nov


def structure_novelty(feats: np.ndarray, embed: int, k_frac: float) -> np.ndarray:
    """Serra et al. structure features at bar level: time-delay embedding, mutual k-NN
    recurrence, circular time-lag matrix, Gaussian smoothing; novelty = change between
    consecutive lag rows. Peaks mark where the song's repetition pattern changes."""
    n = feats.shape[0]
    if n < 4:
        return np.zeros(n + 1)
    k = max(2, int(round(k_frac * n)))
    total = np.zeros(n + 1)
    # A forward embedding (bars b..b+3) sees a boundary up to 3 bars early, a backward one
    # (b-3..b) up to 3 bars late; their sum peaks at the boundary itself.
    for direction in (1, -1):
        idx = np.clip(np.arange(n)[:, None] + direction * np.arange(embed)[None, :], 0, n - 1)
        emb = feats[idx].reshape(n, -1)
        sim = _cos_ssm(emb)
        kth_row = np.sort(sim, axis=1)[:, -k][:, None]
        rec = ((sim >= kth_row) & (sim >= kth_row.T)).astype(np.float64)
        lag = np.zeros((n, n))
        for i in range(n):
            lag[i] = np.roll(rec[i], -i)
        lag = gaussian_filter1d(gaussian_filter1d(lag, sigma=1.0, axis=0), sigma=0.5, axis=1)
        nov = np.zeros(n + 1)
        nov[1:n] = np.sum((lag[1:] - lag[:-1]) ** 2, axis=1)
        total += nov / (nov.max() + 1e-12)
    return total


def pick_boundaries(nov: np.ndarray, n_bars: int, m: MusicSettings) -> list[int]:
    """Bar indices where sections start (always includes 0), greedy by novelty strength.

    Candidates are local maxima (within +-2 bars) above mean + 0.5 sd of the novelty curve.
    A candidate one bar off a 4-bar phrase line moves onto the line when the line's novelty is
    at least 60% of the candidate's (sections usually start on phrase lines)."""
    if n_bars <= m.min_section_bars:
        return [0]
    inner = nov[1:n_bars]
    if inner.size == 0 or inner.max() <= 0:
        return [0]
    thr = inner.mean() + 0.5 * inner.std()
    loc = maximum_filter1d(nov, size=5, mode="constant")
    cand = [b for b in range(1, n_bars) if nov[b] >= thr and nov[b] >= loc[b] - 1e-12]
    snapped = []
    for b in cand:
        for d in (-1, 1):
            q = b + d
            if 0 < q < n_bars and q % 4 == 0 and b % 4 != 0 and nov[q] >= 0.6 * nov[b]:
                b = q
                break
        snapped.append(b)
    chosen: list[int] = []
    for b in sorted(set(snapped), key=lambda b: nov[b], reverse=True):
        if b < m.min_section_bars // 2 or n_bars - b < m.min_section_bars // 2:
            continue
        if all(abs(b - c) >= m.min_section_bars for c in chosen):
            chosen.append(b)
    return [0] + sorted(chosen)


def laplacian_labels(chroma: np.ndarray, timbre: np.ndarray, k: int, seed: int = 0) -> np.ndarray:
    """McFee & Ellis (2014) Laplacian segmentation at bar level; one cluster label per bar.

    Recurrence affinity from time-delay-embedded chroma (mutual k-NN, diagonal median filter),
    plus a sequential affinity from timbre between neighbouring bars, balanced as in the paper;
    the first k eigenvectors of the normalised Laplacian are clustered with k-means."""
    from scipy.cluster.vq import kmeans2
    from scipy.linalg import eigh
    from scipy.ndimage import median_filter

    n = chroma.shape[0]
    if n < k + 2:
        return np.zeros(n, dtype=int)
    idx = np.clip(np.arange(n)[:, None] + np.arange(-1, 2)[None, :], 0, n - 1)
    emb = chroma[idx].reshape(n, -1)
    sim = _cos_ssm(emb)
    dist = 1.0 - sim
    np.fill_diagonal(dist, np.inf)
    for d in (1,):  # neighbouring bars are trivially similar: not a repetition
        dist[np.arange(n - d), np.arange(d, n)] = np.inf
        dist[np.arange(d, n), np.arange(n - d)] = np.inf
    kk = max(2, int(2 * math.ceil(math.sqrt(n - 2))) // 2)
    kth = np.sort(dist, axis=1)[:, kk - 1][:, None]
    mutual = (dist <= kth) & (dist <= kth.T) & np.isfinite(dist)
    bw = np.median(dist[mutual]) if mutual.any() else 1.0
    rec = np.where(mutual, np.exp(-dist / max(bw, 1e-6)), 0.0)
    # Path enhancement: median filter along diagonals (time-lag domain), size 3 bars.
    lag = np.array([np.roll(rec[i], -i) for i in range(n)])
    lag = median_filter(lag, size=(3, 1), mode="nearest")
    rec = np.array([np.roll(lag[i], i) for i in range(n)])
    rec = np.maximum(rec, rec.T)
    # Sequential affinity between consecutive bars from timbre.
    dt = np.sum(np.diff(timbre, axis=0) ** 2, axis=1)
    sig = np.median(dt) if dt.size else 1.0
    path = np.zeros((n, n))
    w = np.exp(-dt / max(sig, 1e-6))
    path[np.arange(n - 1), np.arange(1, n)] = w
    path[np.arange(1, n), np.arange(n - 1)] = w
    deg_p, deg_r = path.sum(axis=1), rec.sum(axis=1)
    mu = deg_p.dot(deg_p + deg_r) / max(np.sum((deg_p + deg_r) ** 2), 1e-12)
    a = mu * rec + (1 - mu) * path
    deg = a.sum(axis=1)
    dinv = 1.0 / np.sqrt(np.maximum(deg, 1e-12))
    lap = np.eye(n) - (dinv[:, None] * a * dinv[None, :])
    _, evecs = eigh(lap)
    evecs = median_filter(evecs, size=(3, 1), mode="nearest")
    cnorm = np.cumsum(evecs ** 2, axis=1) ** 0.5
    x = evecs[:, :k] / (cnorm[:, k - 1: k] + 1e-12)
    rng = np.random.default_rng(seed)
    _, labels = kmeans2(x, k, minit="++", seed=rng)
    return labels.astype(int)


def labels_to_segments(labels: np.ndarray, sim_feats: np.ndarray, min_bars: int) -> tuple[list[int], list[int]]:
    """Boundaries where the bar label changes; segments shorter than `min_bars` are merged into
    the more similar neighbour. Returns (start bars, label per segment)."""
    n = labels.size
    starts = [0] + [b for b in range(1, n) if labels[b] != labels[b - 1]]
    segs = [[st, (starts[i + 1] if i + 1 < len(starts) else n), int(labels[st])] for i, st in enumerate(starts)]
    f = sim_feats / (np.linalg.norm(sim_feats, axis=1, keepdims=True) + 1e-12)
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, (a, b, lab) in enumerate(segs):
            if b - a >= min_bars:
                continue
            mean = f[a:b].mean(axis=0)
            def sim(j):
                return float(mean @ f[segs[j][0]:segs[j][1]].mean(axis=0))
            nbrs = [j for j in (i - 1, i + 1) if 0 <= j < len(segs)]
            j = max(nbrs, key=sim)
            lo, hi = min(i, j), max(i, j)
            keep_label = segs[j][2]
            segs[lo:hi + 1] = [[segs[lo][0], segs[hi][1], keep_label]]
            changed = True
            break
    # Merge neighbours that ended up with the same label.
    out = [segs[0]]
    for a, b, lab in segs[1:]:
        if lab == out[-1][2]:
            out[-1][1] = b
        else:
            out.append([a, b, lab])
    return [a for a, _, _ in out], [lab for _, _, lab in out]


def segmentation_score(feats: np.ndarray, bounds: list[int], labels: list[int], n_bars: int,
                       target_bars: float = 8.0) -> float:
    """Model selection for the number of section types: segments sharing a label should be
    similar, segments with different labels should differ, and sections should be phrase-sized
    (about 8 bars; penalty per octave away from that)."""
    segs = [(bounds[i], bounds[i + 1] if i + 1 < len(bounds) else n_bars) for i in range(len(bounds))]
    f = feats / (np.linalg.norm(feats, axis=1, keepdims=True) + 1e-12)
    means = np.array([f[a:b].mean(axis=0) for a, b in segs])
    means = means / (np.linalg.norm(means, axis=1, keepdims=True) + 1e-12)
    sim = means @ means.T
    same, diff = [], []
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            (same if labels[i] == labels[j] else diff).append(sim[i, j])
    within = float(np.mean(same)) if same else 0.0
    between = float(np.mean(diff)) if diff else 0.0
    mean_len = n_bars / max(1, len(segs))
    return (within - between) - 0.1 * abs(math.log2(max(mean_len, 1e-6) / target_bars))


def refine_boundaries(bounds: list[int], nov: np.ndarray, n_bars: int, window: int, min_gap: int) -> list[int]:
    """Move each boundary to the strongest local novelty within +-window bars (4-bar phrase lines
    get a 15% preference), keeping boundaries in order and at least `min_gap` bars apart."""
    out = [0]
    for i, b in enumerate(bounds[1:], start=1):
        nxt = bounds[i + 1] if i + 1 < len(bounds) else n_bars
        lo = max(out[-1] + min_gap, b - window)
        hi = min(nxt - min_gap, b + window, n_bars - 1)
        if hi < lo:
            out.append(b)
            continue
        cands = list(range(lo, hi + 1))
        best = max(cands, key=lambda q: (nov[q] * (1.15 if q % 4 == 0 else 1.0), -abs(q - b)))
        out.append(best)
    return out


def _segment_similarity(f: np.ndarray, a: tuple[int, int], b: tuple[int, int]) -> float:
    """Best mean cosine similarity of aligned bar sequences (shorter slid along the longer)."""
    A, B = f[a[0]:a[1]], f[b[0]:b[1]]
    if len(A) > len(B):
        A, B = B, A
    if len(A) == 0:
        return 0.0
    best = -1.0
    for off in range(0, len(B) - len(A) + 1):
        best = max(best, float(np.mean(np.sum(A * B[off: off + len(A)], axis=1))))
    return best


def label_sections(bar_feats: np.ndarray, bounds: list[int], n_bars: int, energy: np.ndarray,
                   m: MusicSettings, given: Optional[list[int]] = None) -> list[tuple[int, int, str, str, float]]:
    """[(start_bar, end_bar, label, kind, energy)]. `given` = a cluster label per segment."""
    segs = [(bounds[i], bounds[i + 1] if i + 1 < len(bounds) else n_bars) for i in range(len(bounds))]
    f = bar_feats / (np.linalg.norm(bar_feats, axis=1, keepdims=True) + 1e-12)
    labels: list[int] = []
    reps: list[int] = []  # representative segment index per label
    if given is not None:
        remap: dict[int, int] = {}
        labels = [remap.setdefault(g, len(remap)) for g in given]
    for i, sg in enumerate(segs if given is None else []):
        best, best_l = -1.0, -1
        for l, r in enumerate(reps):
            sim = _segment_similarity(f, sg, segs[r])
            if sim > best:
                best, best_l = sim, l
        if best >= m.same_label_similarity:
            labels.append(best_l)
        else:
            reps.append(i)
            labels.append(len(reps) - 1)
    seg_energy = [float(energy[a:b].mean()) if b > a else 0.0 for a, b in segs]
    count = {l: labels.count(l) for l in set(labels)}
    mean_e = {l: float(np.mean([seg_energy[i] for i in range(len(segs)) if labels[i] == l])) for l in set(labels)}
    repeated = [l for l in count if count[l] >= 2]
    chorus = max(repeated, key=lambda l: mean_e[l]) if repeated else None
    kinds: list[str] = []
    for i, l in enumerate(labels):
        kind = "section"
        if chorus is not None and l == chorus:
            kind = "chorus"
        elif i == 0 and (chorus is None or labels.count(l) == 1):
            kind = "intro"
        elif i == len(segs) - 1 and labels.count(l) == 1:
            kind = "outro"
        elif l in repeated:
            kind = "verse"
            nxt = labels[i + 1] if i + 1 < len(labels) else None
            if nxt == chorus and segs[i][1] - segs[i][0] <= 4 and count[l] >= 2:
                kind = "pre-chorus"
        elif chorus is not None and i > 0 and labels[i - 1] == chorus and i < len(segs) - 1:
            kind = "bridge"
        kinds.append(kind)
    letters = {}
    out = []
    for i, (sg, l) in enumerate(zip(segs, labels)):
        if l not in letters:
            letters[l] = chr(ord("A") + len(letters)) if len(letters) < 26 else f"S{len(letters)}"
        out.append((sg[0], sg[1], letters[l], kinds[i], seg_energy[i]))
    return out


# --------------------------------------------------------------------------------------
# Public
# --------------------------------------------------------------------------------------

def analyse_music(audio: np.ndarray, s: EngineSettings, m: Optional[MusicSettings] = None) -> MusicMap:
    m = m or MusicSettings()
    sr = s.analysis_sr
    duration = audio.size / sr
    rms_db = 20 * np.log10(np.sqrt(np.mean(np.asarray(audio, dtype=np.float64) ** 2)) + 1e-12) if audio.size else -999.0
    if rms_db < s.silence_dbfs:
        return MusicMap(duration_s=round(duration, 6), tempo_bpm=0.0, tempo_confidence=0.0, beats_per_bar=m.beats_per_bar,
                        beats=[], downbeats=[], beat_energy=[],
                        sections=[SongSection(index=0, start_s=0.0, end_s=round(duration, 6), bars=0, label="A",
                                              kind="section", energy=0.0)])
    full, low, fps = onset_envelope(audio, s)
    env = _normalise(full, fps)
    bpm, tconf = estimate_tempo(env, fps, m)
    beat_frames = track_beats(env, fps, bpm, m)
    if beat_frames.size < 2 * m.beats_per_bar:
        return MusicMap(duration_s=round(duration, 6), tempo_bpm=round(bpm, 3), tempo_confidence=round(tconf, 3),
                        beats_per_bar=m.beats_per_bar, beats=[], downbeats=[], beat_energy=[],
                        sections=[SongSection(index=0, start_s=0.0, end_s=round(duration, 6), bars=0, label="A",
                                              kind="section", energy=0.5)])
    # Refine the tempo from the tracked beats: least-squares slope of beat time vs beat number
    # (frame quantisation of single intervals would bias it by up to 1%).
    kk = np.arange(beat_frames.size)
    ibi = float(np.polyfit(kk, beat_frames / fps, 1)[0])
    bpm = 60.0 / ibi if ibi > 0 else bpm
    beat_frames = extend_grid(beat_frames, ibi * fps, audio, s)
    beat_s = beat_frames / fps
    hop = s.hop_length
    beat_samples = np.append(beat_frames * hop, min(audio.size, int(beat_frames[-1] * hop + ibi * sr)))
    beat_chroma, beat_rms = interval_features(audio, sr, beat_samples)
    low_n = _normalise(low, fps)
    low_at = np.array([low_n[max(0, f - 2): f + 3].max() for f in beat_frames])
    phase, dconf = choose_downbeat_phase(beat_chroma, low_at, m.beats_per_bar)
    bpb = m.beats_per_bar
    db_idx = np.arange(phase, beat_frames.size, bpb)
    downbeats = beat_s[db_idx]

    # Loudness per beat (dB), mapped to 0..1 between the 5th and 95th percentile.
    db = 20 * np.log10(beat_rms + 1e-9)
    lo_db, hi_db = np.percentile(db, 5), np.percentile(db, 95)
    beat_energy = np.clip((db - lo_db) / max(hi_db - lo_db, 1e-6), 0, 1)

    # Bars = downbeat to downbeat. Features per bar: chroma, timbre (mel DCT), loudness.
    n_bars = len(db_idx) - 1
    if n_bars < 1:
        sections = [SongSection(index=0, start_s=0.0, end_s=round(duration, 6), bars=0, label="A", kind="section", energy=0.5)]
    else:
        lm = log_mel(audio, s, s.fmax_hz)
        bar_chroma = np.zeros((n_bars, 12))
        bar_timbre = np.zeros((n_bars, 12))
        bar_energy = np.zeros(n_bars)
        for b in range(n_bars):
            i0, i1 = db_idx[b], db_idx[b + 1]
            c = beat_chroma[i0:i1].mean(axis=0)
            bar_chroma[b] = c / (np.linalg.norm(c) + 1e-12)
            f0, f1 = beat_frames[i0], beat_frames[i1]
            bar_timbre[b] = dct(lm[f0:f1].mean(axis=0), type=2, norm="ortho")[1:13]
            bar_energy[b] = beat_energy[i0:i1].mean()
        tz = (bar_timbre - bar_timbre.mean(axis=0)) / (bar_timbre.std(axis=0) + 1e-6)
        if m.segmentation == "laplacian":
            min_gap = max(2, m.min_section_bars // 2)
            sim_feats = np.hstack([bar_chroma, tz * 0.25])
            best = None
            for k in (m.laplacian_k,) if m.laplacian_k else (3, 4, 5, 6):
                lab = laplacian_labels(bar_chroma, tz, k=k)
                b_k, g_k = labels_to_segments(lab, sim_feats, min_gap)
                sc = segmentation_score(sim_feats, b_k, g_k, n_bars)
                if best is None or sc > best[0] + 1e-9:
                    best = (sc, b_k, g_k)
            bounds, given = best[1], best[2]
            if m.refine_window_bars > 0:
                local = foote_novelty(_cos_ssm(np.hstack([bar_chroma, tz * 0.5, (bar_energy[:, None] - bar_energy.mean()) * 4.0])), 2)
                bounds = refine_boundaries(bounds, local, n_bars, m.refine_window_bars, min_gap)
            labelled = label_sections(np.hstack([bar_chroma, tz * 0.25]), bounds, n_bars, bar_energy, m, given=given)
        else:
            labelled = None
        homog = foote_novelty(_cos_ssm(np.hstack([tz * 0.5, (bar_energy[:, None] - bar_energy.mean()) * 4.0])),
                              m.novelty_half_bars)
        struct = structure_novelty(np.hstack([bar_chroma, tz * 0.25]), embed=4, k_frac=0.15)
        def unit(v):
            return v / v.max() if v.max() > 0 else v
        nov = 0.6 * unit(struct) + 0.4 * unit(homog)
        if labelled is None:
            bounds = pick_boundaries(nov, n_bars, m)
            labelled = label_sections(np.hstack([bar_chroma, tz * 0.25]), bounds, n_bars, bar_energy, m)
        sections = []
        bar_starts = list(downbeats[:n_bars]) + [float(downbeats[n_bars])]
        for k, (a, b, lab, kind, e) in enumerate(labelled):
            st = 0.0 if k == 0 else bar_starts[a]
            en = duration if k == len(labelled) - 1 else bar_starts[b]
            sections.append(SongSection(index=k, start_s=round(st, 6), end_s=round(en, 6), bars=int(b - a),
                                        label=lab, kind=kind, energy=round(e, 4)))
    return MusicMap(
        duration_s=round(duration, 6), tempo_bpm=round(float(bpm), 3), tempo_confidence=round(tconf, 3),
        beats_per_bar=bpb, beats=[round(float(t), 4) for t in beat_s],
        downbeats=[round(float(t), 4) for t in downbeats],
        beat_energy=[round(float(e), 4) for e in beat_energy], sections=sections,
    )


def music_map_for_file(path: str, s: EngineSettings, cache_dir: Optional[str] = None,
                       m: Optional[MusicSettings] = None) -> MusicMap:
    """Decode the master and build its music map, cached on disk by file hash + settings."""
    import hashlib
    import json
    import os

    from .analyze import sha256_file
    from .decode import decode_mono

    m = m or MusicSettings()
    cache_file = None
    if cache_dir:
        key = hashlib.sha256(f"{sha256_file(path)}|music{MUSIC_MAP_REVISION}|{m.model_dump_json()}|"
                             f"{s.analysis_sr}|{s.hop_length}|{s.n_fft}".encode()).hexdigest()[:24]
        cache_file = os.path.join(cache_dir, "music", f"{key}.json")
        if os.path.isfile(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return MusicMap.model_validate(json.load(f))
            except Exception:
                pass
    mm = analyse_music(decode_mono(path, s.analysis_sr), s, m)
    if cache_file:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        tmp = cache_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(mm.model_dump_json())
        os.replace(tmp, cache_file)
    return mm

