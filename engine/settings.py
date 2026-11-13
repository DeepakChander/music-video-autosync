"""Every tunable number of the sync engine lives here (rule 7: no magic numbers).

Each value carries a comment saying where it came from:
  - "spec start value"  = the starting point given in the build prompt, section 5
  - "calibrated"        = set from the synthetic calibration run recorded in docs/DECISIONS.md (D-008)
  - "design"            = an engineering choice explained in the comment

Changing any value changes `settings_hash()`, which invalidates cached clip results, so a
re-run never mixes results computed under different settings.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class TierThresholds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Minimum coarse feature correlation (normalised, -1..1) at the chosen position.
    score: float
    # Minimum ratio best / best-distinct-alternative (>1 s away or another rate).
    peak_ratio: float
    # Minimum fraction of validation windows that agree within 1 frame.
    # None = tier reachable without window validation (clips shorter than window_min_clip_s).
    window_agreement: Optional[float]


class EngineSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # --- decode / features -------------------------------------------------------------
    # spec start value: one analysis rate, 16 kHz.
    analysis_sr: int = 16000
    # design: 25 ms analysis window, 512-point FFT, 10 ms hop (spec says hop 10-16 ms).
    win_length: int = 400
    n_fft: int = 512
    hop_length: int = 160
    # design: 40 mel bands 80 Hz .. 7.6 kHz; bands above the content limit of a rate-r
    # match are dropped (see feature_fmax_for_rate) so a 2x clip is not compared on empty bands.
    n_mels: int = 40
    fmin_hz: float = 80.0
    fmax_hz: float = 7600.0
    # design: MFCC c0..c19 (c0 kept: after per-coefficient standardisation it tracks the
    # loudness contour of the song, which survives room playback well).
    n_mfcc: int = 20
    # design: weight of the standardised onset-strength envelope relative to one MFCC dim.
    onset_weight: float = 3.0
    # design: a clip whose audio RMS is below this is treated as having no usable audio.
    silence_dbfs: float = -70.0

    # --- search ------------------------------------------------------------------------
    # spec default (approved by Deepak 2026-11-13): search playback rates 1.0 and 2.0.
    rates: tuple[float, ...] = (1.0, 2.0)
    # spec start value: keep top K = 5 coarse peaks per rate.
    coarse_top_k: int = 5
    # spec start value: non-maximum suppression and "distinct candidate" separation, 1 s.
    nms_separation_s: float = 1.0
    # design: a lag is only scored when clip and song overlap by at least this much...
    min_overlap_s: float = 2.0
    # ...and by at least this fraction of the clip (stops tiny edge overlaps from scoring high).
    min_overlap_fraction: float = 0.4
    # spec start value: fine search +-200 ms around each coarse candidate.
    fine_search_s: float = 0.2
    # design: fine alignment uses at most this much audio (centre of the overlap).
    fine_max_segment_s: float = 30.0
    # design: GCC-PHAT peak (0..1) below this means the fine pass found nothing; keep coarse.
    fine_min_peak: float = 0.02
    # design: fine pass may only move the position this far from the coarse estimate.
    fine_max_shift_s: float = 0.05

    # --- clip rules --------------------------------------------------------------------
    # spec start value: clips shorter than 2 s are TOO_SHORT.
    min_clip_s: float = 2.0
    # spec start value: window validation for clips of at least 6 s (song time).
    window_min_clip_s: float = 6.0
    # spec: 3 to 5 windows.
    min_windows: int = 3
    max_windows: int = 5
    # spec: agreement tolerance is 1 sequence frame.
    agreement_tolerance_frames: float = 1.0
    # calibrated (holdout seed 3): correct drift fits had RMS residual <= 0.27 ms, the two wrong
    # ones 3.8 and 4.9 ms. Above this the drift slope is reported as unreliable (ppm = null).
    drift_max_residual_ms: float = 1.0

    # --- confidence (calibrated on synthetic set seed 2, 400 takes; docs/DECISIONS.md D-008) ---
    # PROVISIONAL until the real 30-take set is run (Phase 1 gate); Deepak approves after the
    # Phase 1 report.
    # calibrated: unrelated-song clips scored at most 0.209 (stage 1 proposal 0.26); 0.30 adds
    # margin for real-world unrelated audio. 1% of correct clips score below 0.456.
    noise_floor: float = 0.30
    # calibrated: every wrong position came from audio repeated exactly elsewhere in the song,
    # with peak_ratio <= 1.041; 1.15 leaves a 0.11 margin. ~3% of correct clips fall under it
    # and are flagged AMBIGUOUS (LOW) instead -- the safe direction.
    ambiguity_ratio: float = 1.15
    # calibrated: with floor/ambiguity applied, 237/400 clips were placeable and none was wrong,
    # so HIGH/MEDIUM are set by evidence strength: correct clips had score p01 0.456 / p50 0.634
    # and ratio p05 1.18 / p50 1.87; 6 of 237 had window agreement below 1.
    high: TierThresholds = TierThresholds(score=0.45, peak_ratio=1.50, window_agreement=1.0)
    medium: TierThresholds = TierThresholds(score=0.35, peak_ratio=1.15, window_agreement=0.6)
    # design: HIGH also needs the sample-level fine pass to have succeeded, so a HIGH position
    # is never only the 10 ms coarse estimate.
    high_requires_fine: bool = True

    # --- policy ------------------------------------------------------------------------
    # Approved by Deepak 2026-11-13 (section 12): a clip that starts before the song gets a
    # source in-point so the song lines up; the full media stays available as handles.
    pre_roll_policy: Literal["set_in_point"] = "set_in_point"

    # --- execution (not part of the result hash) -----------------------------------------
    max_workers: Optional[int] = Field(default=None, exclude=True)

    def feature_fmax_for_rate(self, rate: float) -> float:
        """Highest mel frequency that carries song content after undoing a rate-r speed-up."""
        nyquist_after_stretch = self.analysis_sr / (2.0 * rate)
        return min(self.fmax_hz, 0.95 * nyquist_after_stretch)

    def public_dict(self) -> dict:
        return self.model_dump(mode="json")

    def settings_hash(self) -> str:
        blob = json.dumps(self.public_dict(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    def feature_hash(self, rate: float) -> str:
        keys = ["analysis_sr", "win_length", "n_fft", "hop_length", "n_mels", "fmin_hz",
                "fmax_hz", "n_mfcc", "onset_weight"]
        d = {k: getattr(self, k) for k in keys}
        d["rate"] = rate
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


DEFAULT_SETTINGS = EngineSettings()


def load_settings(path: Optional[str] = None, overrides: Optional[dict] = None) -> EngineSettings:
    data: dict = {}
    if path:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})
    return EngineSettings(**data)
