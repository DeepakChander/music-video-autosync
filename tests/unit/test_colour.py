"""Colour maths, matching and .cube LUTs (Adobe Cube LUT spec 1.0), checked against FFmpeg's reader."""

import subprocess

import numpy as np
import pytest

from engine.colour import (LOOKS, apply_lut, build_lut, delta_e, lab_stats, lab_to_rgb, look_rgb, match_transfer,
                           read_cube, rgb_to_lab, write_cube)
from tests.conftest import needs_ffmpeg


def scene(seed=0, cast=(0.0, 0.0, 0.0), gain=1.0, h=90, w=160):
    """A smooth colourful test image with an optional colour cast and exposure gain."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
    img = np.stack([0.3 + 0.4 * x, 0.25 + 0.5 * y, 0.5 - 0.3 * x * y], axis=-1)
    img += 0.03 * rng.standard_normal(img.shape)
    img = img * gain + np.array(cast)
    return np.clip(img, 0, 1)


def test_lab_round_trip():
    rgb = np.random.default_rng(1).random((500, 3))
    assert np.max(np.abs(lab_to_rgb(rgb_to_lab(rgb)) - rgb)) < 1e-6
    white = rgb_to_lab(np.array([1.0, 1.0, 1.0]))
    assert abs(white[0] - 100) < 1e-3 and abs(white[1]) < 1e-3 and abs(white[2]) < 1e-3


def test_match_removes_a_cast_and_an_exposure_difference():
    ref = scene(1)
    off = scene(2, cast=(0.08, 0.0, -0.06), gain=0.8)
    s_ref, s_off = lab_stats(ref), lab_stats(off)
    before = delta_e(s_off, s_ref)
    lut = build_lut(match_transfer(s_off, s_ref))
    fixed = apply_lut(lut, off.reshape(-1, 3)).reshape(off.shape)
    after = delta_e(lab_stats(fixed), s_ref)
    assert before > 8
    assert after < 0.3 * before


def test_match_is_clamped_so_a_take_is_balanced_not_repainted():
    ref = scene(1)
    night = scene(3, gain=0.25)
    t = match_transfer(lab_stats(night), lab_stats(ref))
    shift_l = t.ref_mean[0] - t.src_mean[0]
    assert shift_l <= 15.0 + 1e-9
    assert all(0.7 - 1e-9 <= s <= 1.4 + 1e-9 for s in t.scale)


def test_identity_and_cube_round_trip(tmp_path):
    ident = build_lut(None, "none", size=17)
    rgb = np.random.default_rng(4).random((300, 3))
    assert np.max(np.abs(apply_lut(ident, rgb) - rgb)) < 1e-6
    p = write_cube(str(tmp_path / "id.cube"), ident, "identity")
    text = open(p).read().splitlines()
    assert text[1] == "LUT_3D_SIZE 17" and text[2].startswith("DOMAIN_MIN")
    assert np.allclose(read_cube(p), ident, atol=1e-6)


@pytest.mark.parametrize("look", LOOKS)
def test_looks_stay_in_range(look):
    rgb = np.random.default_rng(5).random((1000, 3))
    out = look_rgb(look, rgb.copy())
    assert out.min() >= 0 and out.max() <= 1
    if look == "bw_contrast":
        assert np.max(np.abs(out[:, 0] - out[:, 1])) < 0.02


@needs_ffmpeg
def test_ffmpeg_reads_our_cube_the_same_way(tmp_path, ffmpeg_bin):
    """Channel order and axis order of the .cube must match what FFmpeg (and the spec) expect:
    apply a strongly non-symmetric LUT with FFmpeg and with apply_lut and compare."""
    img = (scene(7, h=64, w=64) * 255).round().astype(np.uint8)
    src = tmp_path / "in.rgb"
    src.write_bytes(img.tobytes())
    table = build_lut(match_transfer(lab_stats(scene(8, cast=(0.1, -0.05, 0.0))), lab_stats(scene(1))), "teal_orange")
    cube = write_cube(str(tmp_path / "t.cube"), table, "test")
    out = subprocess.run([ffmpeg_bin, "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x64", "-i", str(src),
                          "-vf", f"lut3d=file={cube}:interp=trilinear", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, stdout=subprocess.PIPE).stdout
    ff = np.frombuffer(out, dtype=np.uint8).reshape(64, 64, 3).astype(np.float64) / 255
    ours = apply_lut(table, img.reshape(-1, 3) / 255.0).reshape(64, 64, 3)
    assert np.max(np.abs(ff - ours)) < 3 / 255


def test_stats_decide_8_bit_by_type_not_by_value():
    img = scene(3)
    a = lab_stats(img)
    b = lab_stats(np.clip(img * 1.0000001, 0, 1.0000001))  # a float a hair above 1.0
    c = lab_stats((img * 255).round().astype(np.uint8))
    assert b is not None and abs(a.mean[0] - b.mean[0]) < 0.01
    assert abs(a.mean[0] - c.mean[0]) < 0.5


def test_faces_drive_the_match_when_both_takes_have_them():
    """A close-up and a wide shot differ in background, not in colour balance: with faces the
    transfer is a pure shift that puts this take's skin on the hero's skin."""
    from engine.colour import LabStats
    src = LabStats(mean=[30.0, -2.0, -12.0], std=[8.0, 4.0, 6.0], pixels=1000)   # dark blue background
    ref = LabStats(mean=[55.0, 1.0, 2.0], std=[20.0, 9.0, 9.0], pixels=1000)
    src_face = LabStats(mean=[58.0, 16.0, 14.0], std=[5.0, 3.0, 3.0], pixels=100)
    ref_face = LabStats(mean=[60.0, 14.0, 18.0], std=[5.0, 3.0, 3.0], pixels=100)
    t = match_transfer(src, ref, src_face=src_face, ref_face=ref_face)
    assert t.scale == [1.0, 1.0, 1.0]
    out = t.apply_lab(np.array(src_face.mean))
    assert np.allclose(out, ref_face.mean, atol=1e-9)
    g = match_transfer(src, ref)  # without faces: whole-frame transfer, clamped
    assert g.ref_mean[0] - g.src_mean[0] <= 15.0 + 1e-9
