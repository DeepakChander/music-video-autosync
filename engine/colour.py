"""Colour matching between takes and creative looks, written as 3D LUTs (.cube).

Method (docs/EDIT_DESIGN.md): Reinhard et al. (2001) mean/standard-deviation transfer in CIE Lab,
the statistics taken from mid-tone pixels of each take; every take is matched to one hero take.
The change is clamped so a take shot in different light is balanced, not repainted.

LUT format: Adobe Cube LUT Specification 1.0 (LUT_3D_SIZE, DOMAIN_MIN/DOMAIN_MAX, red changes
fastest). Values are Rec.709 / sRGB-encoded RGB in 0..1. 33 points per axis.
Premiere cannot be given a LUT through its API (docs/EDIT_DESIGN.md section 2): the LUTs are
applied in the preview video, and in Premiere through Lumetri > Basic Correction > Input LUT.
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np
from pydantic import BaseModel, ConfigDict

# D65 white, sRGB primaries (Rec.709 shares them).
_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]])
_M_XYZ2RGB = np.linalg.inv(_M_RGB2XYZ)
_WHITE = np.array([0.95047, 1.0, 1.08883])


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def _f(t):
    d = 6 / 29
    return np.where(t > d ** 3, np.cbrt(t), t / (3 * d * d) + 4 / 29)


def _finv(t):
    d = 6 / 29
    return np.where(t > d, t ** 3, 3 * d * d * (t - 4 / 29))


def mat3(x: np.ndarray, m: np.ndarray) -> np.ndarray:
    """(..., 3) @ m.T written as per-channel arithmetic. Matrix products go through BLAS, which
    crashed (Windows access violation) when shot-analysis threads called it at the same time."""
    x0, x1, x2 = x[..., 0], x[..., 1], x[..., 2]
    return np.stack([m[r, 0] * x0 + m[r, 1] * x1 + m[r, 2] * x2 for r in range(3)], axis=-1)


def luma(x: np.ndarray) -> np.ndarray:
    """Rec.709 luma of (..., 3) RGB, per-channel arithmetic (no BLAS)."""
    return 0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """(..., 3) encoded RGB 0..1 -> (..., 3) Lab (L 0..100)."""
    xyz = mat3(srgb_to_linear(rgb), _M_RGB2XYZ) / _WHITE
    fx, fy, fz = _f(xyz[..., 0]), _f(xyz[..., 1]), _f(xyz[..., 2])
    return np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], axis=-1)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    xyz = np.stack([_finv(fx), _finv(fy), _finv(fz)], axis=-1) * _WHITE
    return linear_to_srgb(mat3(xyz, _M_XYZ2RGB))


class LabStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mean: list[float]   # L, a, b
    std: list[float]
    pixels: int = 0


def lab_stats(rgb: np.ndarray, lo: float = 0.04, hi: float = 0.96) -> Optional[LabStats]:
    """Statistics of the mid-tone pixels (clipped blacks/whites carry no colour information)."""
    px = rgb.reshape(-1, 3)
    # 8-bit input by type, never by value (a float a hair above 1.0 is still 0..1 data)
    px = px.astype(np.float64) / 255.0 if np.issubdtype(px.dtype, np.integer) else np.clip(px.astype(np.float64), 0.0, 1.0)
    y = luma(px)
    keep = (y > lo) & (y < hi)
    if keep.sum() < 50:
        return None
    lab = rgb_to_lab(px[keep])
    return LabStats(mean=[float(x) for x in lab.mean(axis=0)], std=[float(x) for x in lab.std(axis=0)], pixels=int(keep.sum()))


def pool_stats(stats: list[LabStats]) -> Optional[LabStats]:
    """Combine per-frame statistics into one (pixel-weighted mean and total variance)."""
    stats = [s for s in stats if s is not None and s.pixels > 0]
    if not stats:
        return None
    w = np.array([s.pixels for s in stats], dtype=np.float64)
    mu = np.array([s.mean for s in stats])
    sd = np.array([s.std for s in stats])
    m = (w[:, None] * mu).sum(axis=0) / w.sum()
    var = (w[:, None] * (sd ** 2 + (mu - m) ** 2)).sum(axis=0) / w.sum()
    return LabStats(mean=[float(x) for x in m], std=[float(x) for x in np.sqrt(var)], pixels=int(w.sum()))


class Transfer(BaseModel):
    """Lab' = (Lab - src_mean) * scale + ref_mean, per channel, clamped."""
    model_config = ConfigDict(extra="forbid")
    src_mean: list[float]
    scale: list[float]
    ref_mean: list[float]

    def apply_lab(self, lab: np.ndarray) -> np.ndarray:
        return (lab - np.array(self.src_mean)) * np.array(self.scale) + np.array(self.ref_mean)


class MatchSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    # design: balance, not repaint. Contrast (std) change at most x0.7..x1.4, brightness shift at
    # most 15 L units (~ a stop in mid-tones), colour cast shift at most 12 a/b units.
    scale_min: float = 0.7
    scale_max: float = 1.4
    max_shift_l: float = 15.0
    max_shift_ab: float = 12.0
    strength: float = 1.0      # 0 = no change, 1 = full match


def match_transfer(src: LabStats, ref: LabStats, ms: Optional[MatchSettings] = None,
                   src_face: Optional[LabStats] = None, ref_face: Optional[LabStats] = None) -> Transfer:
    """When both takes show faces, the skin is matched: a pure Lab shift that moves this take's
    skin onto the hero's skin (colourists match skin tones; Premiere's Color Match also weights
    faces). Whole-frame statistics would mostly measure the background, which differs between a
    close-up and a wide shot for reasons that have nothing to do with colour balance.
    Without faces: Reinhard mean/std transfer of the whole frame. Both clamped."""
    ms = ms or MatchSettings()
    s_mu, s_sd = np.array(src.mean), np.maximum(np.array(src.std), 1e-3)
    r_mu, r_sd = np.array(ref.mean), np.array(ref.std)
    if src_face is not None and ref_face is not None:
        scale = np.ones(3)
        shift = np.array(ref_face.mean) - np.array(src_face.mean)
    else:
        scale = np.clip(r_sd / s_sd, ms.scale_min, ms.scale_max)
        shift = r_mu - s_mu
    shift[0] = np.clip(shift[0], -ms.max_shift_l, ms.max_shift_l)
    shift[1:] = np.clip(shift[1:], -ms.max_shift_ab, ms.max_shift_ab)
    k = ms.strength
    scale = 1 + k * (scale - 1)
    target = s_mu + k * shift
    return Transfer(src_mean=list(map(float, s_mu)), scale=list(map(float, scale)), ref_mean=list(map(float, target)))


# --------------------------------------------------------------------------------------
# Looks (applied after the match). Each is a function on encoded RGB in 0..1.
# --------------------------------------------------------------------------------------

def _smooth_contrast(x: np.ndarray, amount: float) -> np.ndarray:
    """S-curve around mid grey; amount 0 = identity."""
    return np.clip(x + amount * (x - 0.5) * (1 - np.abs(2 * x - 1)), 0, 1)


def look_rgb(name: str, rgb: np.ndarray) -> np.ndarray:
    if name in ("", "none", None):
        return rgb
    lab = rgb_to_lab(rgb)
    if name == "warm":
        lab[..., 1] += 3.0
        lab[..., 2] += 10.0
        out = lab_to_rgb(lab)
        return _smooth_contrast(out, 0.10)
    if name == "cool":
        lab[..., 1] -= 2.0
        lab[..., 2] -= 10.0
        return _smooth_contrast(lab_to_rgb(lab), 0.10)
    if name == "teal_orange":
        # warm the skin/highlights, push shadows towards teal
        t = np.clip(lab[..., 0] / 100.0, 0, 1)
        lab[..., 1] += (t - 0.5) * 10.0
        lab[..., 2] += (t - 0.5) * 30.0
        lab[..., 1:] *= 1.1
        return _smooth_contrast(lab_to_rgb(lab), 0.18)
    if name == "bw_contrast":
        lab[..., 1:] = 0.0
        return _smooth_contrast(lab_to_rgb(lab), 0.35)
    if name == "film_fade":
        lab[..., 1:] *= 0.8
        out = lab_to_rgb(lab)
        return np.clip(0.06 + out * 0.9, 0, 1)  # lifted blacks, softer whites
    raise ValueError(f"unknown look {name!r}")


LOOKS = ["none", "warm", "cool", "teal_orange", "bw_contrast", "film_fade"]


# --------------------------------------------------------------------------------------
# LUTs
# --------------------------------------------------------------------------------------

def lut_grid(size: int = 33) -> np.ndarray:
    """(size**3, 3) RGB grid in .cube order: red changes fastest, then green, then blue."""
    v = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(v, v, v, indexing="ij")
    return np.stack([r.ravel(), g.ravel(), b.ravel()], axis=1)


def build_lut(transfer: Optional[Transfer], look: str = "none", size: int = 33) -> np.ndarray:
    rgb = lut_grid(size)
    if transfer is not None:
        rgb = lab_to_rgb(transfer.apply_lab(rgb_to_lab(rgb)))
    rgb = look_rgb(look, rgb)
    return np.clip(rgb, 0.0, 1.0)


def write_cube(path: str, table: np.ndarray, title: str) -> str:
    size = round(len(table) ** (1 / 3))
    if size ** 3 != len(table):
        raise ValueError("table is not a cube")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="ascii", newline="\n") as f:
        safe = "".join(ch for ch in title if ch.isprintable() and ch != '"')
        f.write(f'TITLE "{safe}"\n')
        f.write(f"LUT_3D_SIZE {size}\n")
        f.write("DOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n")
        for r, g, b in table:
            f.write(f"{r:.6f} {g:.6f} {b:.6f}\n")
    os.replace(tmp, path)
    return path


def read_cube(path: str) -> np.ndarray:
    rows = []
    size = None
    with open(path, encoding="ascii") as f:
        for line in f:
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            if t.startswith("LUT_3D_SIZE"):
                size = int(t.split()[1])
            elif t[0].isdigit() or t[0] in "-.":
                rows.append([float(x) for x in t.split()[:3]])
    a = np.array(rows)
    if size is None or len(a) != size ** 3:
        raise ValueError(f"{path}: not a valid 3D cube")
    return a


def apply_lut(table: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    """Trilinear lookup (for tests and checks; FFmpeg/Premiere do their own interpolation)."""
    size = round(len(table) ** (1 / 3))
    cube = table.reshape(size, size, size, 3)  # [b][g][r]
    x = np.clip(rgb, 0, 1) * (size - 1)
    i0 = np.floor(x).astype(int)
    i1 = np.minimum(i0 + 1, size - 1)
    f = x - i0
    out = np.zeros_like(x)
    for dr in (0, 1):
        for dg in (0, 1):
            for db in (0, 1):
                ir = i1[..., 0] if dr else i0[..., 0]
                ig = i1[..., 1] if dg else i0[..., 1]
                ib = i1[..., 2] if db else i0[..., 2]
                w = (f[..., 0] if dr else 1 - f[..., 0]) * (f[..., 1] if dg else 1 - f[..., 1]) * (f[..., 2] if db else 1 - f[..., 2])
                out += w[..., None] * cube[ib, ig, ir]
    return out


def delta_e(a: LabStats, b: LabStats) -> float:
    """CIE76 distance between two mean colours (a summary, not a per-pixel metric)."""
    return float(np.linalg.norm(np.array(a.mean) - np.array(b.mean)))


# --------------------------------------------------------------------------------------
# Colour plan for a set of takes
# --------------------------------------------------------------------------------------

class TakeColour(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clip_id: str
    name: str
    delta_e_before: float
    delta_e_after: float           # predicted from the transfer (mean colour vs the hero)
    match_lut: Optional[str] = None
    final_lut: Optional[str] = None  # match + look


class ColourPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hero: str
    hero_name: str
    look: str
    folder: str
    look_lut: Optional[str] = None
    takes: dict[str, TakeColour]


def choose_hero(stats: dict[str, LabStats], exposure: dict[str, float], sizes: dict[str, Optional[str]]) -> str:
    """The most typical well-exposed take (the medoid of the takes' mean colours), wide or medium
    preferred: matching everything to it changes each take as little as possible."""
    ids = sorted(stats)
    good = [k for k in ids if exposure.get(k, 0.5) >= 0.5] or ids
    wide = [k for k in good if sizes.get(k) in ("wide", "medium")] or good
    means = {k: np.array(stats[k].mean) for k in ids}
    return min(wide, key=lambda k: (sum(np.linalg.norm(means[k] - means[j]) for j in ids), k))


def plan_colour(stats: dict[str, LabStats], names: dict[str, str], folder: str, look: str = "none",
                hero: Optional[str] = None, exposure: Optional[dict[str, float]] = None,
                sizes: Optional[dict[str, Optional[str]]] = None, ms: Optional[MatchSettings] = None,
                size: int = 33, face_stats: Optional[dict[str, Optional[LabStats]]] = None) -> ColourPlan:
    if look not in LOOKS:
        raise ValueError(f"unknown look {look!r}; choose from {', '.join(LOOKS)}")
    stats = {k: v for k, v in stats.items() if v is not None}
    if not stats:
        raise ValueError("no take has usable colour statistics (run shot analysis first)")
    hero = hero if hero in stats else choose_hero(stats, exposure or {}, sizes or {})
    os.makedirs(folder, exist_ok=True)
    ref = stats[hero]
    used: set[str] = set()

    def fname(cid: str, suffix: str) -> str:
        base = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in os.path.splitext(names.get(cid, cid))[0])[:60]
        n, k = f"{base}{suffix}.cube", 2
        while n in used:
            n, k = f"{base}_{k}{suffix}.cube", k + 1
        used.add(n)
        return os.path.join(folder, n)

    takes: dict[str, TakeColour] = {}
    for cid in sorted(stats):
        fs = face_stats or {}
        t = match_transfer(stats[cid], ref, ms, fs.get(cid), fs.get(hero)) if cid != hero else None
        after = (np.array(t.ref_mean) if t else np.array(ref.mean))
        tc = TakeColour(clip_id=cid, name=names.get(cid, cid), delta_e_before=round(delta_e(stats[cid], ref), 3),
                        delta_e_after=round(float(np.linalg.norm(after - np.array(ref.mean))), 3))
        tc.match_lut = write_cube(fname(cid, "_match"), build_lut(t, "none", size), f"AutoSync match {tc.name}")
        if look != "none":
            tc.final_lut = write_cube(fname(cid, f"_match_{look}"), build_lut(t, look, size), f"AutoSync {look} {tc.name}")
        else:
            tc.final_lut = tc.match_lut
        takes[cid] = tc
    look_lut = write_cube(os.path.join(folder, f"look_{look}.cube"), build_lut(None, look, size), f"AutoSync look {look}") \
        if look != "none" else None
    return ColourPlan(hero=hero, hero_name=names.get(hero, hero), look=look, folder=folder, look_lut=look_lut, takes=takes)


# --------------------------------------------------------------------------------------
# Lumetri's LUT folders (Adobe, "Install Custom LUT files", Premiere Pro 24.0 or later):
#   .../LUTs/Technical -> Lumetri Color > Basic Correction > Input LUT
#   .../LUTs/Creative  -> Lumetri Color > Creative > Look
# Per user: macOS ~/Library/Application Support/Adobe/Common/LUTs, Windows %APPDATA%\Adobe\Common\LUTs.
# Premiere scans them at launch (restart), lists only files directly in the folder (no subfolders),
# and community reports put the list limit near 100 LUTs.
# --------------------------------------------------------------------------------------

def lumetri_lut_root() -> Optional[str]:
    import sys
    env = os.environ.get("AUTOSYNC_LUT_DIR")
    if env:
        return env
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Adobe/Common/LUTs")
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        return os.path.join(base, "Adobe", "Common", "LUTs")
    return None


def _safe(name: str, n: int = 60) -> str:
    return "".join(ch if ch.isalnum() or ch in " -_." else "_" for ch in name).strip()[:n]


def install_luts(plan: "ColourPlan", song: str) -> dict:
    """Copy the take LUTs (match + look) into Technical and the look into Creative so they appear in
    Lumetri's menus after a Premiere restart. This song's earlier AutoSync LUTs are replaced."""
    import shutil
    root = lumetri_lut_root()
    if root is None:
        raise ValueError("Premiere's LUT folders exist on macOS and Windows only")
    tech, crea = os.path.join(root, "Technical"), os.path.join(root, "Creative")
    os.makedirs(tech, exist_ok=True)
    os.makedirs(crea, exist_ok=True)
    prefix = f"AutoSync - {_safe(song, 40)} - "
    for f in os.listdir(tech):
        if f.startswith(prefix) and f.lower().endswith(".cube"):
            os.remove(os.path.join(tech, f))
    files = []
    for tc in plan.takes.values():
        if not tc.final_lut:
            continue
        dst = os.path.join(tech, prefix + _safe(os.path.splitext(tc.name)[0]) + ".cube")
        shutil.copyfile(tc.final_lut, dst)
        files.append(dst)
    look = None
    if plan.look_lut:
        look = os.path.join(crea, f"AutoSync - {plan.look}.cube")
        shutil.copyfile(plan.look_lut, look)
    count = sum(1 for f in os.listdir(tech) if f.lower().endswith(".cube"))
    return {"technical_dir": tech, "creative_dir": crea, "files": files, "look": look, "restart_needed": True,
            "warning": (f"{count} LUTs in {tech}: Premiere lists about 100; remove old ones you no longer need."
                        if count > 90 else None)}
