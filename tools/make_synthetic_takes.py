"""Synthetic take generator (spec section 10).

From a master song, cut clips at known positions, degrade them like a real music video shoot,
wrap them in video files, and write the true positions to truth.csv.

  python tools/make_synthetic_takes.py --out ./synthetic --count 60 --seed 1
  python tools/make_synthetic_takes.py --out ./synthetic --master my_song.wav --count 30

With no --master, a deterministic synthetic song with exactly repeated choruses is generated
(tools/synth_song.py), which makes every repeated position known.

truth.csv columns
  name, file, case, expected_reason, true_position_s, rate, ambiguous_positions,
  unique_fraction, drift_ppm, duration_s, fps
true_position_s is the song position of the clip's first video frame BEFORE the pre-roll policy
(negative when the take starts before the song). It is audio-aligned: it includes the
speaker-to-microphone propagation delay (acoustic_delay_ms), because that delay is physically
present in the camera's scratch audio.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, fftconvolve, resample, resample_poly, sosfilt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.synth_song import SR, Song, make_song  # noqa: E402

CASES_DEFAULT = [
    # (case, weight)
    ("clean", 10), ("room", 22), ("lossy", 12), ("preroll", 10), ("chorus", 8), ("slowmo", 8),
    ("drift", 6), ("short", 6), ("noaudio", 3), ("unrelated", 7), ("silent", 2), ("corrupt", 1),
    ("avoffset", 3), ("long", 2),
]

FPS_CHOICES = ["24000/1001", "24000/1001", "24000/1001", "25", "30000/1001", "24"]


@dataclass
class Take:
    name: str
    file: str
    case: str
    expected_reason: str
    true_position_s: Optional[float]
    rate: float
    ambiguous_positions: list[float]
    unique_fraction: float
    drift_ppm: float
    duration_s: float
    fps: str
    acoustic_delay_ms: float = 0.0


def _ff(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + " ".join(cmd) + "\n" + proc.stderr.decode()[-800:])


def write_wav(path: str, audio: np.ndarray, sr: int = SR) -> None:
    a = np.clip(audio, -1.0, 1.0)
    wavfile.write(path, sr, (a * 32767.0).astype(np.int16))


def pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    f = np.arange(spec.size)
    spec[1:] /= np.sqrt(f[1:])
    out = np.fft.irfft(spec, n)
    return out / (np.std(out) + 1e-12)


def babble(n: int, rng: np.random.Generator, sr: int = SR) -> np.ndarray:
    """Crude talking-like noise: formant-filtered pulse trains with syllable envelopes."""
    out = np.zeros(n)
    t = np.arange(n) / sr
    for _ in range(3):
        f0 = rng.uniform(95, 220)
        ph = np.cumsum(2 * np.pi * f0 * (1 + 0.05 * np.sin(2 * np.pi * rng.uniform(2, 5) * t)) / sr)
        src = np.sign(np.sin(ph)) * 0.5 + 0.1 * rng.standard_normal(n)
        lo = rng.uniform(300, 800)
        sos = butter(2, [lo, lo + rng.uniform(800, 2000)], "band", fs=sr, output="sos")
        syll = (np.sin(2 * np.pi * rng.uniform(3, 6) * t + rng.uniform(0, 6)) > 0.1).astype(float)
        out += sosfilt(sos, src) * sosfilt(butter(1, 20, "low", fs=sr, output="sos"), syll)
    return out / (np.std(out) + 1e-12)


# Direct-to-reverberant ratio range. A camera a few metres from a playback speaker sits near
# the room's critical distance: roughly -10 dB (far, live room) to +6 dB (close, dead room).
DRR_DB_RANGE = (-10.0, 6.0)


def room_rir(rng: np.random.Generator, sr: int = SR) -> tuple[np.ndarray, float]:
    """Room impulse response: direct path (gain 1) after a propagation delay, then early
    reflections and an exponential tail scaled to a random direct-to-reverberant ratio."""
    rt60 = rng.uniform(0.2, 0.7)
    n = int(rt60 * sr)
    t = np.arange(n) / sr
    rev = rng.standard_normal(n) * np.exp(-6.9 * t / rt60)
    rev[: int(0.003 * sr)] = 0.0
    for _ in range(4):  # early reflections, 4-30 ms after the direct sound
        k = int(rng.uniform(0.004, 0.03) * sr)
        rev[k] += rng.uniform(2.0, 5.0) * rng.choice([-1, 1])
    drr_db = rng.uniform(*DRR_DB_RANGE)
    rev *= np.sqrt(1.0 / (10 ** (drr_db / 10.0) * np.sum(rev ** 2)))
    rev[0] = 1.0
    delay_s = float(rng.uniform(0.002, 0.010))  # speaker-to-mic distance 0.7-3.4 m
    rir = np.concatenate([np.zeros(int(round(delay_s * sr))), rev])
    return rir, int(round(delay_s * sr)) / sr


def degrade_room(x: np.ndarray, rng: np.random.Generator, snr_db: Optional[float] = None,
                 sr: int = SR) -> tuple[np.ndarray, float]:
    """Playback through a small speaker, picked up by a camera mic in a room, with a performer.

    Returns (audio, acoustic_delay_s). The delay is physical: the camera mic hears the song
    acoustic_delay_s after the speaker plays it, so audio-aligned truth includes it.
    """
    lo, hi = rng.uniform(120, 250), rng.uniform(6000, 9000)
    y = sosfilt(butter(2, [lo, hi], "band", fs=sr, output="sos"), x)
    rir, delay_s = room_rir(rng, sr)
    y = fftconvolve(y, rir)[: x.size]
    p_sig = np.mean(y ** 2) + 1e-12
    snr = rng.uniform(0, 20) if snr_db is None else snr_db
    noise = 0.6 * pink_noise(x.size, rng) + 0.4 * babble(x.size, rng, sr)
    noise *= np.sqrt(p_sig / (10 ** (snr / 10)) / (np.mean(noise ** 2) + 1e-12))
    perf = babble(x.size, rng, sr) * np.sqrt(p_sig) * rng.uniform(0.2, 0.6)  # performer singing along
    y = y + noise + perf
    gain = 10 ** (rng.uniform(-20, 4) / 20)
    y = y / (np.max(np.abs(y)) + 1e-9) * 0.9 * gain
    return np.clip(y, -1, 1), delay_s


_degrade_room = degrade_room


def unique_fraction(song: Song, start: float, length: float) -> float:
    """Fraction of [start, start+length) that lies in sections without exact copies."""
    families: dict[str, int] = {}
    for s in song.sections:
        fam = s.copy_of or s.name
        families[fam] = families.get(fam, 0) + 1
    covered = 0.0
    for s in song.sections:
        a, b = max(start, s.start_s), min(start + length, s.end_s)
        if b > a and families[s.copy_of or s.name] == 1:
            covered += b - a
    inside = max(0.0, min(start + length, song.duration_s) - max(start, 0.0))
    return covered / inside if inside > 0 else 0.0


def allocate_cases(count: int, case_filter: Optional[list[str]] = None) -> list[str]:
    """Spread `count` takes over the cases in proportion to their weights (largest remainder),
    giving every case at least one take when count allows."""
    weights = [(c, w) for c, w in CASES_DEFAULT if not case_filter or c in case_filter]
    total = sum(w for _, w in weights)
    exact = [(c, count * w / total) for c, w in weights]
    n = {c: int(x) for c, x in exact}
    if count >= len(weights):
        for c, _ in weights:
            n[c] = max(1, n[c])
    while sum(n.values()) > count:
        c = max(n, key=lambda k: (n[k], k))
        n[c] -= 1
    rema = sorted(exact, key=lambda cx: -(cx[1] - int(cx[1])))
    i = 0
    while sum(n.values()) < count:
        n[rema[i % len(rema)][0]] += 1
        i += 1
    return [c for c, _ in weights for _ in range(n[c])]


def chorus_sections(song: Song):
    return [s for s in song.sections if (s.copy_of or s.name).startswith("chorus")]


def build(out: str, count: int, seed: int, master_path: Optional[str], video_codec: str,
          container: str, case_filter: Optional[list[str]] = None, size: str = "160x90") -> list[Take]:
    rng = np.random.default_rng(seed)
    os.makedirs(os.path.join(out, "takes"), exist_ok=True)
    os.makedirs(os.path.join(out, "master"), exist_ok=True)
    if master_path:
        sr_in, data = wavfile.read(master_path)
        data = data.astype(np.float32)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if np.issubdtype(np.asarray(data).dtype, np.integer) or np.max(np.abs(data)) > 1.5:
            data = data / 32768.0
        if sr_in != SR:
            data = resample_poly(data, SR, sr_in).astype(np.float32)
        song = Song(audio=data, sr=SR, bpm=0.0, sections=[])
        master_out = os.path.abspath(master_path)
    else:
        song = make_song(seed=7)
        master_out = os.path.join(out, "master", "song_master.wav")
        write_wav(master_out, song.audio)
    other = make_song(seed=99, bpm=96.0, key=5, layout="alt")
    dur = song.duration_s

    order = allocate_cases(count, case_filter)
    rng.shuffle(order)

    takes: list[Take] = []
    tmpdir = tempfile.mkdtemp(prefix="mvas_")
    for i, case in enumerate(order):
        name = f"T{i + 1:03d}_{case}"
        fps = str(rng.choice(FPS_CHOICES))
        rate, drift_ppm, expected = 1.0, 0.0, ""
        length = float(rng.uniform(6, 30))
        start = float(rng.uniform(0, max(1.0, dur - length)))
        truth: Optional[float] = start
        pre = None
        audio: Optional[np.ndarray] = None
        av_delay = 0.0
        delays: list[float] = []

        def degrade_room(x, rng, snr_db=None, _delays=delays):  # noqa: F811 - records the delay
            y, d = _degrade_room(x, rng, snr_db)
            _delays.append(d)
            return y

        def seg(a: float, b: float, src: np.ndarray = song.audio) -> np.ndarray:
            ia, ib = int(round(a * SR)), int(round(b * SR))
            return src[max(0, ia):max(0, ib)].astype(np.float64)

        if case == "clean":
            audio = seg(start, start + length)
        elif case == "room":
            audio = degrade_room(seg(start, start + length), rng)
        elif case == "lossy":
            audio = degrade_room(seg(start, start + length), rng, snr_db=float(rng.uniform(10, 25)))
        elif case == "preroll":
            pre = float(rng.uniform(1, 5))
            if rng.random() < 0.5:
                start = 0.0  # take starts before the song: negative position
            body = seg(start, start + length)
            lead = babble(int(pre * SR), rng) * 0.05 + pink_noise(int(pre * SR), rng) * 0.01
            audio = degrade_room(np.concatenate([lead, body]), rng, snr_db=float(rng.uniform(8, 20)))
            truth = start - pre
        elif case == "chorus":
            sec = chorus_sections(song)[int(rng.integers(0, len(chorus_sections(song))))]
            length = float(rng.uniform(4, min(14, sec.end_s - sec.start_s - 0.5)))
            start = float(rng.uniform(sec.start_s, sec.end_s - length))
            audio = degrade_room(seg(start, start + length), rng, snr_db=float(rng.uniform(10, 25)))
            truth = start
        elif case == "slowmo":
            rate = 2.0
            fps = str(rng.choice(["48", "60000/1001"]))
            song_len = float(rng.uniform(10, 40))
            start = float(rng.uniform(0, dur - song_len))
            fast = resample_poly(seg(start, start + song_len), 1, 2)  # playback at 2x
            audio = degrade_room(fast, rng, snr_db=float(rng.uniform(8, 25)))
            truth = start
            length = audio.size / SR
        elif case == "drift":
            drift_ppm = float(rng.choice([-100.0, 100.0]))
            length = float(rng.uniform(20, 40))
            start = float(rng.uniform(0, dur - length))
            x = seg(start, start + length)
            # Camera clock fast by +ppm records (1+ppm) samples per true second.
            n_new = int(round(x.size * (1 + drift_ppm * 1e-6)))
            audio = degrade_room(resample(x, n_new), rng, snr_db=float(rng.uniform(15, 25)))
            truth = start
        elif case == "short":
            length = float(rng.uniform(0.5, 3.0))
            start = float(rng.uniform(0, dur - length))
            audio = degrade_room(seg(start, start + length), rng, snr_db=float(rng.uniform(15, 25)))
            truth = start
            if length < 2.0:
                expected = "TOO_SHORT"
        elif case == "noaudio":
            expected, truth, audio = "NO_AUDIO", None, None
        elif case == "silent":
            expected, truth = "NO_AUDIO", None
            audio = np.zeros(int(length * SR))
        elif case == "unrelated":
            expected, truth = "NO_MATCH", None
            o_start = float(rng.uniform(0, other.duration_s - length))
            audio = degrade_room(seg(o_start, o_start + length, other.audio), rng, snr_db=float(rng.uniform(5, 25)))
        elif case == "corrupt":
            expected, truth = "DECODE_ERROR", None
        elif case == "avoffset":
            av_delay = float(rng.choice([0.1, 0.25, -0.15]))
            audio = degrade_room(seg(start, start + length), rng, snr_db=float(rng.uniform(15, 25)))
            # Audio stream starts av_delay after video; video frame 0 is av_delay before the song cue.
            truth = start - av_delay
        elif case == "long":
            length = float(rng.uniform(90, min(200, dur - 1)))
            start = float(rng.uniform(0, dur - length))
            audio = degrade_room(seg(start, start + length), rng, snr_db=float(rng.uniform(5, 20)))
            truth = start
        else:
            raise ValueError(case)

        if audio is not None:
            length = audio.size / SR
        acoustic_delay = sum(delays)
        if truth is not None and acoustic_delay:
            # Clip sample t holds song time start + rate * (t - delay): the take begins
            # rate * delay earlier in song time than the cue that was played.
            truth -= rate * acoustic_delay
        song_start = start
        song_len = (length - (pre or 0.0)) * rate
        lossy = case == "lossy"
        audio_only = container == "wav" and case not in ("noaudio", "avoffset", "corrupt")
        if lossy:
            if audio_only:
                ext = str(rng.choice([".mp3", ".m4a"]))
            else:
                ext = ".mov" if video_codec == "prores" else ".mp4"  # ProRes is not allowed in MP4
        elif audio_only:
            ext = ".wav"
        else:
            ext = ".mov"
        path = os.path.join(out, "takes", name + ext)
        wav_tmp = os.path.join(tmpdir, name + ".wav")
        if audio is not None:
            write_wav(wav_tmp, audio)
        if case == "corrupt":
            with open(path, "wb") as f:
                f.write(b"\x00\x00\x00\x18ftypqt  " + rng.bytes(4096))
        elif audio_only and audio is not None:
            if ext == ".wav":
                os.replace(wav_tmp, path)
            elif ext == ".mp3":
                _ff(["ffmpeg", "-y", "-v", "error", "-i", wav_tmp, "-c:a", "libmp3lame", "-b:a", "64k", path])
            else:
                _ff(["ffmpeg", "-y", "-v", "error", "-i", wav_tmp, "-c:a", "aac", "-b:a", "64k", path])
        else:
            vlen = max(length, 0.5) if audio is not None else length
            vcodec = (["-c:v", "prores_ks", "-profile:v", "0", "-pix_fmt", "yuv422p10le"]
                      if video_codec == "prores" else ["-c:v", "mpeg4", "-q:v", "12"])
            cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}"]
            if audio is not None:
                if av_delay:
                    cmd += ["-itsoffset", f"{av_delay}"]
                cmd += ["-i", wav_tmp, "-map", "0:v", "-map", "1:a"]
            cmd += ["-t", f"{vlen:.6f}"] + vcodec
            if audio is not None:
                cmd += (["-c:a", "aac", "-b:a", "64k"] if lossy else ["-c:a", "pcm_s16le"])
            cmd += [path]
            _ff(cmd)
        amb = song.repeats_of(song_start, song_len) if (truth is not None and song.sections) else []
        uf = unique_fraction(song, song_start, song_len) if (truth is not None and song.sections) else 1.0
        takes.append(Take(name=name, file=os.path.relpath(path, out), case=case, expected_reason=expected,
                          true_position_s=None if truth is None else round(truth, 6), rate=rate,
                          ambiguous_positions=[round(p, 6) for p in amb] if len(amb) > 1 else [],
                          unique_fraction=round(uf, 4), drift_ppm=drift_ppm, duration_s=round(length, 4),
                          fps=fps, acoustic_delay_ms=round(acoustic_delay * 1000, 4)))
        if os.path.exists(wav_tmp):
            os.remove(wav_tmp)

    shutil.rmtree(tmpdir, ignore_errors=True)
    with open(os.path.join(out, "truth.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "file", "case", "expected_reason", "true_position_s", "rate",
                    "ambiguous_positions", "unique_fraction", "drift_ppm", "duration_s", "fps",
                    "acoustic_delay_ms"])
        for t in takes:
            w.writerow([t.name, t.file, t.case, t.expected_reason,
                        "" if t.true_position_s is None else f"{t.true_position_s:.6f}", t.rate,
                        ";".join(f"{p:.6f}" for p in t.ambiguous_positions), t.unique_fraction,
                        t.drift_ppm, t.duration_s, t.fps, t.acoustic_delay_ms])
    with open(os.path.join(out, "master_path.txt"), "w", encoding="utf-8") as f:
        f.write(os.path.abspath(master_out) + "\n")
    return takes


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--count", type=int, default=60)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--master", help="use this WAV as the master instead of the synthetic song")
    p.add_argument("--video-codec", choices=["prores", "mpeg4"], default="prores",
                   help="prores (imports into Premiere on Windows and macOS) or mpeg4 (smaller)")
    p.add_argument("--container", choices=["mov", "wav"], default="mov",
                   help="mov = video files with audio; wav = audio-only files (faster, for calibration)")
    p.add_argument("--cases", help="comma-separated subset of cases")
    p.add_argument("--size", default="160x90")
    a = p.parse_args(argv)
    takes = build(a.out, a.count, a.seed, a.master, a.video_codec, a.container,
                  a.cases.split(",") if a.cases else None, a.size)
    print(f"wrote {len(takes)} takes to {os.path.abspath(a.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
