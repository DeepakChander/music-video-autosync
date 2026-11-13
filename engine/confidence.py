"""Confidence tiers from calibrated thresholds (spec section 5, step 12)."""

from __future__ import annotations

from typing import Optional

from .result import Reason, Status
from .settings import EngineSettings, TierThresholds


def _meets(t: TierThresholds, score: float, ratio: float, agreement: Optional[float]) -> bool:
    if score < t.score or ratio < t.peak_ratio:
        return False
    if t.window_agreement is None:
        return True
    if agreement is None:
        # No window validation possible (short clip): cannot meet a tier that requires it.
        return False
    return agreement >= t.window_agreement


def tier(score: float, ratio: float, agreement: Optional[float], s: EngineSettings,
         short_clip: bool, fine_used: bool = True) -> tuple[Status, Optional[Reason]]:
    """Map evidence to (status, reason).

    * best score under the noise floor             -> UNSYNCED / NO_MATCH
    * two distinct candidates too close             -> LOW / AMBIGUOUS
    * strong score, clear peak, windows agree       -> HIGH
    * otherwise MEDIUM or LOW
    Short clips (no window validation) can reach MEDIUM at most, and only through the
    medium score / ratio thresholds.
    """
    if score < s.noise_floor:
        return Status.UNSYNCED, Reason.NO_MATCH
    if ratio < s.ambiguity_ratio:
        return Status.LOW, Reason.AMBIGUOUS
    if not short_clip and _meets(s.high, score, ratio, agreement) and (fine_used or not s.high_requires_fine):
        return Status.HIGH, None
    if short_clip:
        if score >= s.medium.score and ratio >= s.medium.peak_ratio:
            return Status.MEDIUM, None
        return Status.LOW, None
    if _meets(s.medium, score, ratio, agreement):
        return Status.MEDIUM, None
    return Status.LOW, None
