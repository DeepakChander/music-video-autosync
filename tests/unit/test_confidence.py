from engine.confidence import tier
from engine.result import Reason, Status
from engine.settings import EngineSettings

S = EngineSettings()


def test_below_floor_is_no_match():
    assert tier(S.noise_floor - 0.01, 9.0, 1.0, S, short_clip=False) == (Status.UNSYNCED, Reason.NO_MATCH)


def test_close_candidates_are_ambiguous_low():
    assert tier(0.9, S.ambiguity_ratio - 0.01, 1.0, S, short_clip=False) == (Status.LOW, Reason.AMBIGUOUS)


def test_strong_evidence_is_high():
    assert tier(0.9, 3.0, 1.0, S, short_clip=False) == (Status.HIGH, None)


def test_high_needs_fine_pass():
    assert tier(0.9, 3.0, 1.0, S, short_clip=False, fine_used=False)[0] == Status.MEDIUM


def test_window_disagreement_blocks_high():
    assert tier(0.9, 3.0, 0.75, S, short_clip=False)[0] == Status.MEDIUM
    assert tier(0.9, 3.0, 0.4, S, short_clip=False)[0] == Status.LOW


def test_short_clip_caps_at_medium():
    assert tier(0.95, 5.0, None, S, short_clip=True) == (Status.MEDIUM, None)
    assert tier(S.medium.score - 0.01, 5.0, None, S, short_clip=True) == (Status.LOW, None)
