"""Local speech-to-text for ad dialogue: whisper.cpp through pywhispercpp (MIT; OpenAI Whisper
weights, MIT). Audio is decoded by FFmpeg as a separate process (engine/decode.py) and handed over
as a numpy array, so no FFmpeg code is loaded into Python (PyAV, which faster-whisper needs, bundles
GPL x264/x265: rejected, docs/DECISIONS.md D-027).

Models are downloaded once from the whisper.cpp model repository at a pinned revision and checked
by SHA-256 before use. Word timestamps: token_timestamps + max_len=1 + split_on_word.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import urllib.request
from typing import Callable, Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .decode import decode_mono

HF_REPO = "ggerganov/whisper.cpp"
HF_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
MODELS = {  # name: (file, bytes, sha256) -- sizes and hashes from the repository's LFS metadata
    "tiny.en": ("ggml-tiny.en.bin", 77704715, "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f"),
    "base.en": ("ggml-base.en.bin", 147964211, "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002"),
    "small.en": ("ggml-small.en.bin", 487614201, "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d"),
}
STT_REVISION = 2  # 2: speech regions, non-speech markers dropped


class SttError(RuntimeError):
    pass


class Word(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    start: float
    end: float
    prob: float = 0.0


class Transcript(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = STT_REVISION
    clip_id: str
    model: str
    words: list[Word] = Field(default_factory=list)
    # Where the audio actually has speech-level sound: [[start, end], ...] seconds. Whisper's word
    # times stretch over silence (first word from 0.0, last word to the end of the segment); line
    # in/out points are pulled onto these regions (engine/ad.py).
    speech: list[list[float]] = Field(default_factory=list)
    error: Optional[str] = None

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def available() -> bool:
    try:
        import pywhispercpp.model  # noqa: F401
        return True
    except Exception:
        return False


def model_file(name: str, models_dir: str) -> str:
    if name not in MODELS:
        raise SttError(f"unknown speech model {name!r}; choose {', '.join(MODELS)}")
    return os.path.join(models_dir, MODELS[name][0])


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(name: str, models_dir: str, progress: Optional[Callable[[int, int], None]] = None) -> str:
    """Path of a verified model file, downloading it once if needed."""
    path = model_file(name, models_dir)
    fname, size, sha = MODELS[name]
    if os.path.isfile(path) and os.path.getsize(path) == size and _sha256(path) == sha:
        return path
    os.makedirs(models_dir, exist_ok=True)
    url = f"https://huggingface.co/{HF_REPO}/resolve/{HF_REVISION}/{fname}"
    tmp = path + ".part"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MusicVideoAutoSync"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, size)
    except Exception as e:
        raise SttError(f"could not download the speech model ({fname}) from huggingface.co: {e}") from e
    if os.path.getsize(tmp) != size or _sha256(tmp) != sha:
        os.remove(tmp)
        raise SttError(f"the downloaded speech model {fname} failed its checksum; nothing was installed")
    os.replace(tmp, path)
    return path


_NON_SPEECH = re.compile(r"^\s*[\[\(].*[\]\)]\s*$")  # [BLANK_AUDIO], (music), [MUSIC] ...


def speech_regions(audio: np.ndarray, sr: int = 16000, hop_s: float = 0.02, min_s: float = 0.08,
                   gap_s: float = 0.25) -> list[list[float]]:
    """Stretches louder than the clip's own noise floor + 12 dB (and above -50 dBFS), short gaps
    bridged, very short blips dropped. Design values; a level gate, not a speech classifier."""
    hop = max(1, int(sr * hop_s))
    n = audio.size // hop
    if n == 0:
        return []
    fr = audio[: n * hop].reshape(n, hop).astype(np.float64)
    db = 10 * np.log10(np.mean(fr ** 2, axis=1) + 1e-12)
    thr = max(np.percentile(db, 10) + 12.0, -50.0)
    on = db > thr
    regions, i = [], 0
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            regions.append([i * hop_s, j * hop_s])
            i = j
        else:
            i += 1
    merged: list[list[float]] = []
    for r in regions:
        if merged and r[0] - merged[-1][1] <= gap_s:
            merged[-1][1] = r[1]
        else:
            merged.append(list(r))
    return [[round(a, 3), round(b, 3)] for a, b in merged if b - a >= min_s]


class Transcriber:
    """One loaded model; transcribe() calls are serialised (one whisper context)."""

    def __init__(self, name: str, models_dir: str, threads: Optional[int] = None):
        if not available():
            raise SttError("speech-to-text is not installed (pywhispercpp); dialogue matching is off")
        from pywhispercpp.model import Model
        self.name = name
        self.path = ensure_model(name, models_dir)
        self.lock = threading.Lock()
        self.model = Model(self.path, n_threads=threads or max(1, (os.cpu_count() or 2) // 2),
                           print_progress=False, print_realtime=False, redirect_whispercpp_logs_to=None)

    def words(self, audio) -> list[Word]:
        with self.lock:
            segs = self.model.transcribe(audio, token_timestamps=True, max_len=1, split_on_word=True,
                                         extract_probability=True)
        out = []
        for s in segs:
            t = (s.text or "").strip()
            if not t or _NON_SPEECH.match(t):
                continue
            out.append(Word(text=t, start=round(s.t0 / 100.0, 3), end=round(s.t1 / 100.0, 3),
                            prob=round(float(getattr(s, "probability", 0.0) or 0.0), 4)))
        return out

    def transcribe_file(self, clip_id: str, path: str) -> Transcript:
        try:
            audio = decode_mono(path, 16000)
        except Exception as e:
            return Transcript(clip_id=clip_id, model=self.name, error=f"audio not readable: {e}")
        if audio.size < 1600:
            return Transcript(clip_id=clip_id, model=self.name, words=[])
        return Transcript(clip_id=clip_id, model=self.name, words=self.words(audio), speech=speech_regions(audio))


def transcripts_for(clips: list[tuple[str, str]], name: str, models_dir: str, cache_dir: Optional[str],
                    progress: Optional[Callable[[int, int, str], None]] = None) -> dict[str, Transcript]:
    """Transcribe clips [(clip_id, path)], cached per clip id + model."""
    out: dict[str, Transcript] = {}
    todo = []
    for cid, path in clips:
        cf = os.path.join(cache_dir, "stt", f"{cid}_{name}_r{STT_REVISION}.json") if cache_dir else None
        if cf and os.path.isfile(cf):
            try:
                with open(cf, encoding="utf-8") as f:
                    out[cid] = Transcript.model_validate(json.load(f))
                continue
            except Exception:
                pass
        todo.append((cid, path, cf))
    if todo:
        tr = Transcriber(name, models_dir)
        for i, (cid, path, cf) in enumerate(todo, start=1):
            t = tr.transcribe_file(cid, path)
            out[cid] = t
            if cf and t.error is None:
                os.makedirs(os.path.dirname(cf), exist_ok=True)
                with open(cf + ".tmp", "w", encoding="utf-8") as f:
                    f.write(t.model_dump_json())
                os.replace(cf + ".tmp", cf)
            if progress:
                progress(len(out), len(clips), path)
    return out
