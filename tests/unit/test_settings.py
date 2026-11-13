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


def test_real_truth_template_is_accepted():
    import csv
    import os
    from fractions import Fraction
    from tools.accuracy_report import normalise_truth
    path = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "real_truth_template.csv")
    with open(path, newline="", encoding="utf-8") as f:
        rows = normalise_truth(list(csv.DictReader(f)), Fraction(24000, 1001))
    assert abs(float(rows[0]["true_position_s"]) - (72 * 24 + 18) * 1001 / 24000) < 1e-6
    assert rows[2]["expected_reason"] == "NO_AUDIO" and rows[2]["true_position_s"] == ""
