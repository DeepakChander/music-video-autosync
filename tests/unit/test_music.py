"""Music map on synthetic songs whose beats, bars and sections are known (tools/synth_song.py)."""

import numpy as np
import pytest
from scipy.signal import resample_poly

from engine.music import MusicMap, analyse_music
from engine.settings import EngineSettings
from tools.music_eval import evaluate, f_measure
from tools.synth_song import make_song


@pytest.fixture(scope="module")
def pop112():
    song = make_song(seed=1, bpm=112.0, layout="pop")
    audio = resample_poly(song.audio, 1, 3).astype(np.float32)
    return song, analyse_music(audio, EngineSettings())


def test_tempo_beats_and_bars(pop112):
    song, mm = pop112
    assert abs(mm.tempo_bpm - 112.0) / 112.0 < 0.01
    beat = round(60.0 / 112.0 * song.sr) / song.sr
    ref = np.arange(0, song.duration_s - 1e-6, beat)
    assert f_measure(np.array(mm.beats), ref) >= 0.95
    bars = ref[::4]
    on_bar = [np.min(np.abs(bars - t)) <= 0.07 for t in mm.downbeats]
    assert np.mean(on_bar) >= 0.95
    assert mm.beats[0] <= 0.07, "the grid reaches back to the first beat of the song"


def test_sections_cover_the_song_in_order(pop112):
    song, mm = pop112
    assert mm.sections[0].start_s == 0.0
    assert mm.sections[-1].end_s == pytest.approx(mm.duration_s)
    for a, b in zip(mm.sections, mm.sections[1:]):
        assert a.end_s == pytest.approx(b.start_s)
        assert b.start_s in mm.downbeats or min(abs(b.start_s - d) for d in mm.downbeats) < 1e-6
    assert any(s.kind == "chorus" for s in mm.sections)


def test_choruses_are_found_on_pop_songs():
    # Averages over three songs: single songs vary (measured 0.40-0.80 chorus time labelled).
    rows = [evaluate(112.0, seed, "pop", EngineSettings()) for seed in (1, 2, 3)]
    assert np.mean([r["chorus_labelled"] for r in rows]) >= 0.6
    assert np.mean([r["bound_recall_3s"] for r in rows]) >= 0.6


def test_deterministic_and_serialisable(pop112):
    song, mm = pop112
    audio = resample_poly(song.audio, 1, 3).astype(np.float32)
    again = analyse_music(audio, EngineSettings())
    assert again.model_dump_json() == mm.model_dump_json()
    assert MusicMap.model_validate_json(mm.model_dump_json()) == mm


def test_silence_gives_an_empty_grid_not_a_crash():
    mm = analyse_music(np.zeros(16000 * 5, dtype=np.float32), EngineSettings())
    assert mm.beats == [] and len(mm.sections) == 1
