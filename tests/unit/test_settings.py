from engine.settings import EngineSettings, load_settings


def test_hash_changes_with_thresholds_and_not_with_workers():
    a = EngineSettings()
    assert a.settings_hash() != EngineSettings(noise_floor=0.31).settings_hash()
    assert a.settings_hash() == EngineSettings(max_workers=3).settings_hash()


def test_overrides(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"rates": [1.0]}')
    s = load_settings(str(p), {"min_clip_s": 3.0})
    assert s.rates == (1.0,) and s.min_clip_s == 3.0
