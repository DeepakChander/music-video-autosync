"""Deterministic synthetic pop song with literally repeated choruses (copy-pasted samples).

Used only for testing: a real song cannot ship in the repo, and a synthetic one lets the truth
of every repeated section be known exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, sosfilt

SR = 48000


@dataclass
class Section:
    name: str
    bars: int
    start_s: float = 0.0
    end_s: float = 0.0
    copy_of: str | None = None  # name of the section whose samples this one repeats exactly


@dataclass
class Song:
    audio: np.ndarray
    sr: int
    bpm: float
    sections: list[Section] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.audio.size / self.sr

    def repeats_of(self, start_s: float, length_s: float) -> list[float]:
        """All song positions whose audio is identical to [start_s, start_s+length_s).

        A segment is ambiguous only when it lies fully inside one section that has exact copies.
        """
        for sec in self.sections:
            if sec.start_s - 1e-6 <= start_s and start_s + length_s <= sec.end_s + 1e-6:
                family = sec.copy_of or sec.name
                out = []
                for other in self.sections:
                    if (other.copy_of or other.name) == family:
                        out.append(other.start_s + (start_s - sec.start_s))
                return sorted(out)
        return [start_s]


def _env(n: int, attack: int, decay: float, sr: int) -> np.ndarray:
    t = np.arange(n) / sr
    e = np.exp(-t / max(decay, 1e-4))
    if attack > 0:
        e[:attack] *= np.linspace(0, 1, attack)
    return e


def _note_freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _saw(f: float, n: int, sr: int, phase: float = 0.0) -> np.ndarray:
    t = np.arange(n) / sr
    return 2.0 * ((f * t + phase) % 1.0) - 1.0


def _tone(f: float, n: int, sr: int, kind: str, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / sr
    if kind == "sine":
        return np.sin(2 * np.pi * f * t)
    if kind == "voice":
        vib = 0.004 * np.sin(2 * np.pi * 5.5 * t)
        ph = np.cumsum(2 * np.pi * f * (1 + vib) / sr)
        return sum((1.0 / k) * np.sin(k * ph) * (1.0 if k < 4 else 0.5) for k in range(1, 8))
    if kind == "pad":
        return (_saw(f, n, sr) + _saw(f * 1.003, n, sr, 0.3) + _saw(f * 0.997, n, sr, 0.6)) / 3.0
    if kind == "square":
        return np.sign(np.sin(2 * np.pi * f * t))
    raise ValueError(kind)


class _Renderer:
    def __init__(self, bpm: float, sr: int, seed: int, key: int):
        self.bpm, self.sr, self.key = bpm, sr, key
        self.rng = np.random.default_rng(seed)
        self.beat = int(round(60.0 / bpm * sr))
        self.bar = self.beat * 4
        self.lp_bass = butter(4, 400, "low", fs=sr, output="sos")
        self.lp_pad = butter(2, 2500, "low", fs=sr, output="sos")
        self.hp_hat = butter(4, 6000, "high", fs=sr, output="sos")
        self.bp_voice = butter(2, [250, 4000], "band", fs=sr, output="sos")

    def drums(self, bars: int, style: int) -> np.ndarray:
        n = bars * self.bar
        out = np.zeros(n)
        sr = self.sr
        kick_n = int(0.25 * sr)
        tk = np.arange(kick_n) / sr
        kick = np.sin(2 * np.pi * (50 + 90 * np.exp(-tk * 30)) * tk) * np.exp(-tk * 9)
        sn_n = int(0.2 * sr)
        snare = (self.rng.standard_normal(sn_n) * 0.6 + np.sin(2 * np.pi * 190 * np.arange(sn_n) / sr) * 0.5) * _env(sn_n, 10, 0.05, sr)
        hat_n = int(0.06 * sr)
        hat = sosfilt(self.hp_hat, self.rng.standard_normal(hat_n)) * _env(hat_n, 5, 0.015, sr)
        eighth = self.beat // 2
        for b in range(bars):
            base = b * self.bar
            kicks = [0, 2] if style == 0 else [0, 1.5, 2, 3.5] if style == 1 else [0, 0.75, 2, 2.5]
            for k in kicks:
                i = base + int(k * self.beat)
                out[i:i + kick_n] += kick[: max(0, min(kick_n, n - i))]
            for k in (1, 3):
                i = base + k * self.beat
                out[i:i + sn_n] += snare[: max(0, min(sn_n, n - i))] * 0.7
            for e in range(8):
                i = base + e * eighth
                acc = 0.35 if e % 2 == 0 else 0.2
                out[i:i + hat_n] += hat[: max(0, min(hat_n, n - i))] * acc
            if b % 4 == 3 and style != 0:  # fill
                for e in range(4):
                    i = base + 2 * self.beat + e * (self.beat // 2)
                    out[i:i + sn_n] += snare[: max(0, min(sn_n, n - i))] * 0.4
        return out

    def chords(self, bars: int, prog: list[int], kind: str = "pad") -> tuple[np.ndarray, np.ndarray]:
        n = bars * self.bar
        pad = np.zeros(n)
        bass = np.zeros(n)
        for b in range(bars):
            root = self.key + prog[b % len(prog)]
            i = b * self.bar
            seg = np.zeros(self.bar)
            for iv in (0, 4, 7) if prog[b % len(prog)] in (0, 5, 7) else (0, 3, 7):
                seg += _tone(_note_freq(root + 48 + iv - 12), self.bar, self.sr, kind, self.rng)
            pad[i:i + self.bar] = seg * _env(self.bar, 800, 3.0, self.sr) / 3.0
            for q in range(4):
                j = i + q * self.beat
                bn = self.beat
                bass[j:j + bn] = _tone(_note_freq(root + 36 - 12 * 0), bn, self.sr, "square", self.rng) * _env(bn, 50, 0.35, self.sr)
        return sosfilt(self.lp_pad, pad), sosfilt(self.lp_bass, bass)

    def melody(self, bars: int, prog: list[int], density: float) -> np.ndarray:
        n = bars * self.bar
        out = np.zeros(n)
        scale = [0, 2, 4, 5, 7, 9, 11]
        eighth = self.beat // 2
        pos = 0
        deg = 4
        while pos < n:
            dur = eighth * int(self.rng.choice([1, 2, 2, 3, 4]))
            if self.rng.random() < density:
                deg = int(np.clip(deg + self.rng.integers(-2, 3), 0, 9))
                b = pos // self.bar
                root = self.key + prog[b % len(prog)]
                midi = root + 60 + scale[deg % 7] + 12 * (deg // 7)
                m = min(dur, n - pos)
                out[pos:pos + m] += _tone(_note_freq(midi), m, self.sr, "voice", self.rng) * _env(m, 300, 0.6, self.sr)
            pos += dur
        return sosfilt(self.bp_voice, out)


def make_song(seed: int = 7, bpm: float = 112.0, key: int = 0, layout: str = "pop") -> Song:
    """Intro, verses (unique), pre-choruses and choruses (exact copies), bridge, outro."""
    r = _Renderer(bpm, SR, seed, key)
    if layout == "pop":
        plan = [("intro", 8, None), ("verse1", 16, None), ("pre1", 4, None), ("chorus1", 8, None),
                ("verse2", 16, None), ("pre2", 4, "pre1"), ("chorus2", 8, "chorus1"), ("bridge", 8, None),
                ("pre3", 4, "pre1"), ("chorus3", 8, "chorus1"), ("chorus4", 8, "chorus1"), ("outro", 8, None)]
    else:
        plan = [("intro", 4, None), ("a1", 12, None), ("b1", 8, None), ("a2", 12, None), ("b2", 8, "b1"), ("outro", 6, None)]
    progs = {"intro": [0, 5, 9, 7], "verse": [9, 5, 0, 7], "pre": [5, 7, 5, 7], "chorus": [0, 7, 9, 5],
             "bridge": [2, 5, 9, 7], "outro": [0, 5, 0, 5], "a": [0, 9, 5, 7], "b": [5, 0, 7, 9]}
    rendered: dict[str, np.ndarray] = {}
    sections: list[Section] = []
    chunks = []
    t = 0
    for name, bars, copy_of in plan:
        if copy_of:
            audio = rendered[copy_of]
        else:
            fam = "".join(ch for ch in name if not ch.isdigit())
            prog = progs.get(fam, [0, 5, 7, 0])
            style = 2 if fam in ("chorus", "b") else 1 if fam in ("verse", "a", "bridge") else 0
            drums = r.drums(bars, style)
            pad, bass = r.chords(bars, prog)
            mel = r.melody(bars, prog, 0.85 if fam in ("chorus", "b") else 0.6 if fam != "intro" else 0.2)
            audio = 0.5 * drums + 0.35 * pad + 0.45 * bass + 0.5 * mel
            rendered[name] = audio
        n = audio.size
        sections.append(Section(name=name, bars=bars, start_s=t / SR, end_s=(t + n) / SR, copy_of=copy_of))
        chunks.append(audio)
        t += n
    song = np.concatenate(chunks)
    song = song / (np.max(np.abs(song)) + 1e-9) * 0.89
    return Song(audio=song.astype(np.float32), sr=SR, bpm=bpm, sections=sections)
