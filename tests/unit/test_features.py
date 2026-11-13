import numpy as np

from engine.features import features, n_frames, stretch
from engine.settings import EngineSettings

S = EngineSettings()


def test_feature_shape_and_standardisation():
    x = np.random.default_rng(0).standard_normal(S.analysis_sr * 3).astype(np.float32)
    f = features(x, S)
    assert f.shape == (n_frames(x.size, S), S.n_mfcc + 1)
    assert np.allclose(f[:, :S.n_mfcc].mean(axis=0), 0, atol=1e-3)


def test_features_are_gain_invariant():
    x = np.random.default_rng(1).standard_normal(S.analysis_sr * 3).astype(np.float32)
    assert np.allclose(features(x, S), features(x * 0.05, S), atol=1e-2)


def test_stretch_by_two_doubles_length():
    x = np.zeros(1000, dtype=np.float32)
    assert stretch(x, 2.0).size == 2000
    assert stretch(x, 1.0).size == 1000
