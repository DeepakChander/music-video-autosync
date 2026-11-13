"""sync_result.json -- the single source of truth (spec section 4).

Ownership rules enforced by structure:
  * Engine fields are written only by engine code. Manual corrections live in `override`.
  * UNSYNCED clips must have song_position_s == None (validator below) -- "never guess" is
    structural, not a convention.

Schema extensions beyond the spec example (all optional, documented in docs/DECISIONS.md D-004):
  reasons NOT_ANALYZED and ANALYSIS_ERROR, reason_detail, song_position_ticks snapping error, source_in_ticks,
  diagnostics, summary.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"


class Status(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNSYNCED = "UNSYNCED"


class Reason(str, Enum):
    NO_AUDIO = "NO_AUDIO"
    NO_MATCH = "NO_MATCH"
    AMBIGUOUS = "AMBIGUOUS"
    TOO_SHORT = "TOO_SHORT"
    DECODE_ERROR = "DECODE_ERROR"
    # Extension: the run was cancelled or interrupted before this clip was analysed.
    NOT_ANALYZED = "NOT_ANALYZED"
    # Extension: an unexpected error inside the engine (bug); the clip is reported, not dropped.
    ANALYSIS_ERROR = "ANALYSIS_ERROR"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class MasterInfo(_Model):
    path: str
    sha256: str
    duration_s: float
    sample_rate: int


class SequenceInfo(_Model):
    fps_num: int
    fps_den: int
    width: int = 3840
    height: int = 2160


class Confidence(_Model):
    score: float = 0.0
    peak_ratio: float = 0.0
    # None when the clip is too short for window validation.
    window_agreement: Optional[float] = None


class Alternate(_Model):
    song_position_s: float
    score: float
    rate: float = 1.0


class Drift(_Model):
    """Reported, never corrected. ppm > 0: the clip's audio clock runs fast relative to the
    master (the clip holds more than one second of audio per second of song), so a clip
    aligned at its centre is early at its tail by ppm * 1e-6 * length.
    ppm is null when the window fit is unreliable (residual above settings.drift_max_residual_ms)."""
    ppm: Optional[float]
    max_error_ms: float
    windows: int
    residual_ms: float = 0.0
    reliable: bool = True


class Override(_Model):
    """Manual correction. Never overwrites engine fields."""
    song_position_s: float
    song_position_ticks: str
    by: str
    at: str
    # Track chosen by the engine's layout function for the overridden position.
    video_track: Optional[int] = None
    note: Optional[str] = None


class Diagnostics(_Model):
    # Song position of the clip's first video frame before the pre-roll policy (can be < 0).
    raw_song_position_s: Optional[float] = None
    # audio stream start - video stream start, seconds (container timestamps).
    av_offset_s: float = 0.0
    # GCC-PHAT peak of the fine pass (0..1); None when the fine pass was not used.
    fine_peak: Optional[float] = None
    fine_used: bool = False
    audio_rms_dbfs: Optional[float] = None
    audio_channels: int = 0
    # Snapped minus exact position, seconds.
    frame_snap_error_s: Optional[float] = None
    windows_evaluated: int = 0


class ClipResult(_Model):
    clip_id: str
    path: str
    name: str
    duration_s: float
    source_fps: Optional[str] = None
    has_audio: bool
    status: Status
    reason: Optional[Reason] = None
    reason_detail: Optional[str] = None
    song_position_s: Optional[float] = None
    song_position_ticks: Optional[str] = None
    source_in_s: float = 0.0
    source_in_ticks: str = "0"
    rate: Optional[float] = None
    confidence: Confidence = Field(default_factory=Confidence)
    alternates: list[Alternate] = Field(default_factory=list)
    drift: Optional[Drift] = None
    video_track: Optional[int] = None
    override: Optional[Override] = None
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)

    @model_validator(mode="after")
    def _never_guess(self) -> "ClipResult":
        if self.status == Status.UNSYNCED:
            if self.song_position_s is not None or self.song_position_ticks is not None:
                raise ValueError("UNSYNCED clips must have song_position_s = null (never guess)")
            if self.reason is None:
                raise ValueError("UNSYNCED clips must carry a reason")
            if self.video_track is not None:
                raise ValueError("UNSYNCED clips must not be assigned a video track")
        else:
            if self.song_position_s is None or self.song_position_ticks is None:
                raise ValueError(f"{self.status.value} clips need a song position")
        return self

    @property
    def effective_position_ticks(self) -> Optional[int]:
        if self.override is not None:
            return int(self.override.song_position_ticks)
        return int(self.song_position_ticks) if self.song_position_ticks is not None else None


class Summary(_Model):
    total: int
    HIGH: int
    MEDIUM: int
    LOW: int
    UNSYNCED: int
    video_tracks: int


class SyncResult(_Model):
    schema_version: str = SCHEMA_VERSION
    engine_version: str
    created_at: str
    master: MasterInfo
    sequence: SequenceInfo
    settings: dict[str, Any]
    clips: list[ClipResult]
    summary: Optional[Summary] = None

    def to_json(self) -> str:
        """Deterministic serialisation: fixed key order, 2-space indent, trailing newline."""
        return json.dumps(self.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"

    def find(self, clip_id: str) -> Optional[ClipResult]:
        for c in self.clips:
            if c.clip_id == clip_id:
                return c
        return None


def json_schema() -> dict:
    schema = SyncResult.model_json_schema()
    schema["$id"] = f"https://music-video-autosync.local/schema/sync_result-{SCHEMA_VERSION}.json"
    return schema


def load_result(path: str) -> SyncResult:
    with open(path, "r", encoding="utf-8") as f:
        return SyncResult.model_validate_json(f.read())
