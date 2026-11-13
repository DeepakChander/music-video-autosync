# Decisions

Each entry: options, evidence, choice, and what would change it (spec rule 12).
Dates are 2026. "Deepak" = product owner who approved section-12 decisions.

---

## D-001 Section 12 open decisions: proposed defaults approved

- **Date / by:** 2026-11-13, approved by Deepak before Phase 1.
- **Choices:**
  | Decision | Choice |
  |---|---|
  | Pre-roll policy | Set the clip's source in-point so the song lines up; full media stays available as handles |
  | Playback rates searched | 1.0 and 2.0, configurable (`engine/settings.py: rates`) |
  | Helper start method | Tray app at login |
  | Unsynced clips | Bin only (`AutoSync / Unsynced`) |
  | Platform for v1 | Windows only (nothing in the design blocks macOS later; the engine and helper already run on macOS for development) |
  | Distribution | Unsigned `.ccx` plus installer, for internal testing |
  | Confidence thresholds | Proposed by calibration (D-008); Deepak approves after the Phase 1 report |
- **What would change it:** client feedback after the first real-project run.

## D-002 Default placement adapter: chosen by an in-panel self-test

- **Problem:** The spec says the default adapter (A: UXP direct, B: FCP7 XML import) must come from
  Phase 0 evidence. The development machine is a Mac without Premiere Pro, so the Phase 0 spikes
  cannot be run here.
- **Options:** (1) default to XML import, (2) default to UXP direct, (3) ship both and let a
  placement self-test on the client's machine decide.
- **Choice (approved by Deepak 2026-11-13):** (3). The panel's **Placement self-test** runs the
  Phase 0 spikes (b) and (c) on the client's own Premiere: it generates N dummy clips through the
  helper, places them with each adapter into new sequences, reads every item back and measures time
  and mismatches. Until it has run, the panel asks which adapter to use; after, it defaults to the
  adapter that passed readback (faster one if both passed). Results are saved and shown.
- **What would change it:** self-test results from the client; if one adapter fails consistently
  it can be hidden.

## D-003 Repository

Private GitHub repository `DeepakChander/music-video-autosync` (approved by Deepak 2026-11-13).
Builds are attached to GitHub Releases.

## D-004 Schema extensions to sync_result.json (schema_version 1.0)

The spec's example is kept field for field. Added, all optional or defaulted:

| Field | Why |
|---|---|
| `reason`: `NOT_ANALYZED` | A cancelled run still lists every clip (non-goal: never silently discard) |
| `reason`: `ANALYSIS_ERROR` | An engine bug on one clip is reported instead of crashing the run or dropping the clip |
| `reason_detail` | Human-readable cause (e.g. "audio stream is silent (-200.0 dBFS)") |
| `source_in_ticks` | The pre-roll in-point in ticks, so the panel never converts floats |
| `drift.residual_ms`, `drift.reliable`, `drift.ppm = null` | See D-009 |
| `diagnostics.*` | Raw (pre-policy) position, A/V offset, fine-pass peak, RMS, frame-snap error: evidence for audits |
| `summary` | Counts for the panel's progress and summary screens |
| `override.video_track`, `override.note` | Track computed by the engine's layout function for an overridden position |

A silent audio stream (RMS below `silence_dbfs`) is reported as `NO_AUDIO` with a detail, because
the spec's acceptance test is "flags clips with no usable audio".

## D-005 Features: hand-written log-mel / MFCC (numpy + scipy), no librosa, no soundfile

- **Options:** librosa; hand-written MFCC.
- **Evidence:** librosa pulls in numba/llvmlite (large, slow PyInstaller builds, version lag on new
  Pythons). The engine needs only STFT, a mel filterbank, a DCT and spectral flux: ~80 lines.
  soundfile is unnecessary because FFmpeg decodes every input (master included) to float32 PCM.
- **Choice:** hand-written (`engine/features.py`), tested for shape, standardisation and gain
  invariance.
- **What would change it:** a need for features not worth writing by hand (e.g. CQT chroma).

## D-006 Fine alignment: GCC-PHAT on the waveform, plain maximum peak

- **Options tested** (scratch experiment, 40 trials of 6-30 s segments through the room simulation,
  error against audio-aligned truth): PHAT max peak; PHAT-beta 0.7; "first arrival" peak picking.
- **Evidence:** all variants: p50 0.03-0.05 ms, max 0.07-0.29 ms; PHAT max peak was best (max 0.07 ms).
- **Choice:** plain GCC-PHAT, band-limited to the content band of the rate being tested, with
  parabolic interpolation. If the fine peak is weak (< `fine_min_peak`) or moves the position more
  than `fine_max_shift_s` from the coarse estimate, the coarse position is kept and the clip cannot
  be rated HIGH (`high_requires_fine`).
- **What would change it:** real takes where the fine pass locks onto reflections (very live rooms,
  direct-to-reverberant ratio below about -15 dB). Then try first-arrival picking.

## D-007 Synthetic generator realism and audio-aligned truth

- **Finding during Phase 1:** the first generator used rooms with a direct-to-reverberant ratio of
  about -20 dB and a truth that ignored speaker-to-microphone propagation delay. Every room take
  then appeared 7-50 ms "early". Investigation showed the engine was right: a clip delayed by 10 ms
  correctly reports -10 ms (test `delay +10ms -> -10.00 ms`).
- **Choice:** truth is **audio-aligned**: it includes the direct-path propagation delay (2-10 ms),
  because that delay is physically present in the scratch audio and is what an audio match can and
  should align. DRR is drawn from -10 to +6 dB (camera near a playback speaker, around the room's
  critical distance). `truth.csv` records `acoustic_delay_ms`.
- **Consequence for users** (in LIMITATIONS.md): positions align the camera's scratch audio with the
  master. A camera 10 m from the playback speaker hears it ~29 ms late; that is inside one frame at
  24 fps and is not corrected.

## D-008 Confidence threshold calibration (PROVISIONAL: synthetic only)

- **Calibration set:** synthetic seed 2, 400 audio-only takes, all 14 cases
  (`tools/calibrate.py`, output `docs/reports/phase1_calibration_seed2.json`).
- **Stage 1** (no floor, no ambiguity cut): 274 correct, 57 wrong positions. Every wrong position was
  a clip lying in audio that repeats exactly elsewhere (unique fraction <= 0.006), with
  peak_ratio <= 1.041. Unrelated-song clips scored at most 0.209.
- **Stage 2** (floor 0.26, ambiguity 1.10 applied): 237 clips placeable, **0 wrong**. Correct clips:
  score p01 0.456 / p50 0.634, peak_ratio p01 1.14 / p05 1.18 / p50 1.87, window agreement < 1 for 6.
- **Chosen values** (margins added for real-world audio the synthetic set does not cover):
  | Setting | Value | Basis |
  |---|---|---|
  | noise_floor | 0.30 | max unrelated 0.209 + margin |
  | ambiguity_ratio | 1.15 | max wrong-copy ratio 1.041 + 0.11 |
  | HIGH | score >= 0.45, ratio >= 1.50, all windows agree, fine pass used | evidence strength |
  | MEDIUM | score >= 0.35, ratio >= 1.15, window agreement >= 0.6 | evidence strength |
  | short clips (< 6 s, no windows) | MEDIUM at most | spec: HIGH needs window agreement |
- **Holdout check:** synthetic seed 3, 200 ProRes video takes, never used for calibration:
  100% of unique takes within 1 frame, 0 wrong HIGH/MEDIUM, 0 unrelated/no-audio placed,
  100% chorus-only flagged or correct, rate search 100% (`docs/reports/phase1_synthetic_holdout_seed3.md`).
- **Status:** PROVISIONAL. The spec requires the real 30-take set; thresholds must be re-checked on
  it with `tools/calibrate.py` (it accepts any set with a truth.csv) before Deepak approves them.
- **What would change it:** any real take rated HIGH or MEDIUM that is wrong by more than 1 frame.

## D-009 Drift reliability

- **Evidence:** on the seed-3 holdout, 10 of 12 drift takes measured within +-15 ppm of the true
  +-100 ppm with fit residual <= 0.27 ms; 2 measured wrongly (one with the wrong sign) with residuals
  3.8 and 4.9 ms. No-drift takes measured |ppm| <= 1.0.
- **Choice:** `drift_max_residual_ms = 1.0`. Above it, `drift.ppm` is null and `reliable` is false,
  so the panel never shows a confidently wrong number. Drift is never corrected.
- **Caveat:** the 1.0 ms cut was chosen on the same holdout it was checked on; re-check on real takes.

## D-010 LOW clips are not placed by default

- **Context:** the spec places "each placed clip"; UNSYNCED clips go to a bin. LOW clips have a
  best-evidence position but weak or ambiguous evidence (e.g. a chorus that repeats).
- **Choice:** the panel places HIGH and MEDIUM clips plus any clip with a manual override. LOW clips
  go to the bin `AutoSync / Needs review` and the review list, where the editor can pick an
  alternate (saved as an override) to place it. A setting "Place low-confidence clips" (off by
  default) places them too.
- **Why:** placing an ambiguous chorus take at its best guess would look correct on the timeline
  while possibly being wrong, which the spec forbids ("never guess a position").
- **What would change it:** Deepak or the client preferring LOW clips on the timeline by default.

## D-011 Reference implementations

- `bbc/audio-offset-finder` (Apache-2.0): read for the idea of standardised MFCC cross-correlation
  and a peak-prominence score. **No code copied.**
- `benfmiller/audalign`: license not checked, so not read for code and **nothing reused**.
- `thinkvp/Syncitol` (MIT): read for Premiere UXP placement quirks (see D-014). **No code copied.**

## D-012 Python dependencies and licenses

Pinned in `requirements*.txt`; licenses in `docs/LICENSES.md`. All permissive (BSD/MIT/Apache)
except `pystray` (LGPL-3.0), used only by the Windows tray launcher. To keep LGPL relinking possible,
the PyInstaller spec ships pystray as loose `.py` files (`module_collection_mode = "py"`) that a
user can replace. FFmpeg is an LGPL build called as a separate process (never linked).
