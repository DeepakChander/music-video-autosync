import json

import pytest
from pydantic import ValidationError

from engine.result import ClipResult, Reason, Status, json_schema


def _base(**kw):
    d = dict(clip_id="x", path="/a.mov", name="a", duration_s=10.0, has_audio=True)
    d.update(kw)
    return d


def test_unsynced_cannot_carry_a_position():
    with pytest.raises(ValidationError):
        ClipResult(**_base(status=Status.UNSYNCED, reason=Reason.NO_MATCH, song_position_s=1.0, song_position_ticks="1"))


def test_unsynced_needs_reason():
    with pytest.raises(ValidationError):
        ClipResult(**_base(status=Status.UNSYNCED))


def test_unsynced_cannot_have_track():
    with pytest.raises(ValidationError):
        ClipResult(**_base(status=Status.UNSYNCED, reason=Reason.NO_AUDIO, video_track=1))


def test_placed_needs_position():
    with pytest.raises(ValidationError):
        ClipResult(**_base(status=Status.HIGH))


def test_schema_has_contract_fields():
    schema = json.dumps(json_schema())
    for field in ("schema_version", "song_position_ticks", "alternates", "override", "window_agreement", "drift"):
        assert field in schema
