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
  (`tools/calibrate.py`, output `docs/reports/phase1_calibration_seed2.json`, algorithm revision 2;
  revisions 3-5 change only tie handling and LOW layout, not scores or ratios).
- **Stage 1** (no floor, no ambiguity cut): 281 correct, 50 wrong positions. Every wrong position was
  a clip lying entirely in audio that repeats exactly elsewhere (unique fraction 0.0), with
  peak_ratio <= 1.017. Unrelated-song clips scored at most 0.209.
- **Stage 2** (floor 0.26, ambiguity 1.10 applied): 238 clips placeable, **0 wrong**. Correct clips:
  score p01 0.440 / p50 0.628, peak_ratio p01 1.127 / p05 1.175 / p50 1.875, window agreement < 1 for 6.
- (Before D-013 the same run gave 57 wrong with max ratio 1.041; the margins below were set then
  and still hold.)
- **Chosen values** (margins added for real-world audio the synthetic set does not cover):
  | Setting | Value | Basis |
  |---|---|---|
  | noise_floor | 0.30 | max unrelated 0.209 + margin |
  | ambiguity_ratio | 1.15 | max wrong-copy ratio 1.041 + 0.11 |
  | HIGH | score >= 0.45, ratio >= 1.50, all windows agree, fine pass used | evidence strength |
  | MEDIUM | score >= 0.35, ratio >= 1.15, window agreement >= 0.6 | evidence strength |
  | short clips (< 6 s, no windows) | MEDIUM at most | spec: HIGH needs window agreement |
- **Holdout checks** (never used for calibration), algorithm revision 5:
  | Set | Sequence | Unique within 1 frame | Wrong HIGH/MEDIUM | Unrelated/no-audio placed | Chorus-only flagged/correct | 2x rate found | 200 clips wall time |
  |---|---|---|---|---|---|---|---|
  | seed 3, 200 ProRes takes | 23.976 | 100% (106/106) | 0 | 0 | 100% | 100% | 15.1 s |
  | seed 4, 200 ProRes takes | 25 | 100% | 0 | 0 | 100% | 100% | 14.5 s |
  Machine: Apple M5, 10 cores (9 workers); mean take 19.4 s. Reports in `docs/reports/`.
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

## D-013 Candidates are rescored at their exact sample position

- **Found by:** the helper's self-test (14 takes, seed 3): a 2.3 s take lying inside a repeated
  pre-chorus was rated MEDIUM at the wrong copy (score ratio 1.17 between identical audio).
- **Cause:** coarse scores compare clip frames with the master's fixed 10 ms frame grid. Copies of a
  section sit at different sub-hop phases of that grid, so identical audio scores differently;
  on short noisy clips the difference reached 17%.
- **Choice:** after the fine pass, every candidate is rescored on master features recomputed on the
  clip's own frame grid at its exact sample position (`locate.rescore`), standardised with the
  master's global statistics. Identical audio now scores within 0.02% (T009: 0.4528 / 0.4527 /
  0.4527) and is flagged AMBIGUOUS. Regression covered by `tests/helper/test_api.py::test_selftest_job_and_accuracy`.
- **Cache safety:** `engine.ALGORITHM_REVISION` is part of every cache key, so results from an older
  algorithm are never reused.

## D-014 Ties between exact repeats

Exact repeats score equal up to float noise, which can differ between CPUs. When a clip is
AMBIGUOUS, the reported position is the earliest candidate within `tie_ratio` (1.005) of the best;
anything further apart keeps the higher score (an earlier version used the full ambiguity ratio and
discarded real evidence on one holdout take, T111). The clip stays LOW with every other copy in
`alternates`, so nothing is guessed.

## D-015 Two-pass track layout

Pass 1 applies the spec rule to HIGH and MEDIUM clips (the ones placed by default). Pass 2 puts LOW
clips into the lowest track with a free gap. Without this, ambiguous chorus takes stacked at one
position pushed placed clips up to 51 tracks on a 200-take set; with it, 21.

## D-016 Helper service

- **Port** 47653 on 127.0.0.1 only (configurable in `%APPDATA%/MusicVideoAutoSync/config.json`).
- **Token:** 32 random bytes in `%APPDATA%/MusicVideoAutoSync/token`, created by the installer
  (`autosync-helper init-token`), required in `X-AutoSync-Token` on every request. The Host header
  must be 127.0.0.1 or localhost (blocks DNS-rebinding from a browser page).
- **Jobs:** one at a time, state in `<cache>/jobs/<id>/job.json`; a job found "running" at start-up is
  marked `interrupted` and can be resumed; finished clips come back from the per-clip cache
  (`tests/helper/test_resume.py` kills the helper with SIGKILL mid-job and resumes).
- **Self-test endpoint:** generates synthetic takes with known truth on the client's machine and
  analyses them, so the whole pipeline can be smoke-tested without the client's footage.

## D-017 FCP7 XML: hand-written xmeml, not the OpenTimelineIO adapter

- **Options:** OpenTimelineIO + otio-fcp-adapter (Apache-2.0); hand-written xmeml.
- **Evidence:** the writer needs exact control of sequence-frame units for mixed rates,
  `file://localhost/D%3a/...` paths and per-clip links (docs/PREMIERE_API_NOTES.md item 12); OTIO
  would add two runtime dependencies to the installer for ~200 lines of output. OTIO's FCP7 reader is
  used in the tests as an independent parser: it reads our XML and sees every clip on the expected
  track at the expected frame (`tests/export/test_fcp7_xml.py`).
- **Not verified:** Premiere's own import of the file. The panel's placement self-test checks it by
  readback on the client's machine.

## D-018 UXP placement design (adapter A)

- **Evidence** (docs/PREMIERE_API_NOTES.md items 1, 5, 6, 7): actions must be created inside
  `lockedAccess` (enforced from 26.3); overwrite never ripples; only insert is documented to create a
  track (index = count); insert ripples; moves are deltas and do not carry linked items; insert and
  overwrite ignore source marks set in the same transaction.
- **Choice:** master on A1 by overwrite at 0. A clip whose tracks exist is placed by overwrite at
  its position. The first clip of a new track is inserted at a staging point 10 minutes past
  everything (nothing there to ripple) and moved to its position: video and scratch audio each by
  the same delta. Source marks are set in their own transaction first and the user's original
  marks restored after. Clips are placed one transaction each, yielding to Premiere every 10 clips;
  the longest block is measured and reported by the self-test (Phase 0 spike b).
- **Never success without readback:** every track item is read back by project item id and start
  ticks; mismatches, missing items, unexpected items and unmuted scratch tracks are all reported.
- **What would change it:** self-test results showing freezes (then larger yields or XML default),
  or negative move deltas failing (then XML default).

## D-019 Panel network permission `"domains": "all"`

Premiere rejects `http://127.0.0.1:<port>` manifest entries (third-party reports, notes item 10);
`"all"` is the documented value. The panel only ever calls `http://127.0.0.1:47653`, and the
helper requires the token and a loopback Host header. Revisit when Adobe documents port syntax.

## D-020 Panel modules are injected, not cross-required

UXP's CommonJS resolution for nested requires is under-documented (notes item 10). Only
`panel/index.js` requires modules (full relative paths with `.js`); every module receives its
dependencies as arguments. The same modules load in Node, where `panel/test/` runs them against a
simulated Premiere (`panel/test/fake-ppro.js`) that enforces the lock rule. `packaging/build_ccx.py`
fails the build if any other file requires a relative module.

## D-021 Installer: per-user, one file for helper and panel

- Inno Setup, `PrivilegesRequired=lowest`, install to `%LOCALAPPDATA%\Programs\MusicVideoAutoSync`
  (no admin rights in studios with locked-down machines).
- Token generated at install (`AutoSyncHelper.exe init-token`); Startup shortcut runs the tray app.
- The panel `.ccx` is installed with Adobe's `UnifiedPluginInstallerAgent /install` when present,
  from a copy in `C:\ProgramData\MusicVideoAutoSync` (UPIA rejects paths with spaces). UPIA is silent,
  so the client guide checks Window > UXP Plugins and falls back to a double-click install.
- Uninstall stops the helper, removes the panel, and deletes program, token, logs and cache.
- The release workflow proves install / run / uninstall on a Windows runner (Phase 5 gate); the
  runner is not a pristine machine, so a client install remains the final check.

## D-022 macOS: run from the Git repository

- **Date / by:** 2026-11-14, Deepak asked for a Mac route (a tester clones the repository). This
  extends D-001 ("Windows only") for run-from-source; the installer stays Windows-only.
- **Evidence:** Adobe's Premiere UXP "Network Operations" recipe: "macOS restricts `http://` for
  security reasons. You should use `https://` instead", and its troubleshooting row "Connection
  fails only on macOS - HTTPS required - Premiere disallows http:// URLs on macOS". An Adobe
  community bug report (Premiere 25.6.3, macOS 26.1) shows `fetch("http://...")` failing. No source
  says whether 127.0.0.1 is exempt.
- **Choice:** the helper serves HTTPS when `config.json` names a certificate; `setup_mac_https.sh`
  makes one with mkcert (a local authority trusted in the user's keychain, certificate valid for
  127.0.0.1 and localhost only). The panel tries `http://127.0.0.1`, then `https://127.0.0.1`, then
  `https://localhost`, and remembers the one that answered, so Windows is unchanged. A no-panel route
  (`engine.cli report`, `python -m export.fcp7_xml`) gives Mac users a working path regardless.
- **Verified on macOS:** fresh clone, `setup_mac.sh`, helper over http and over https (certificate
  verified), helper smoke 7/7, Plan B commands. **Not verified:** Premiere on a Mac accepting the
  certificate; the panel in Premiere on a Mac.
- **What would change it:** the tester's step 8 result (MAC_GUIDE numbering). If Premiere rejects the local certificate,
  Plan B becomes the documented Mac route until another transport is proven.

## D-023 macOS setup installs its own prerequisites

- **Context:** the first Mac tester ran `setup_mac.sh` on a Mac with no Homebrew, FFmpeg or Python
  3.11 (macOS ships Python 3.9). The script stopped and pointed at https://brew.sh, which a
  non-developer cannot act on.
- **Choice:** `setup_mac.sh` installs whatever is missing with Homebrew (`ffmpeg`, `python@3.11`);
  `setup_mac_https.sh` does the same for `mkcert`. When Homebrew itself is missing, the script asks
  before running Homebrew's official installer (only in an interactive Terminal; otherwise it prints
  the command). Homebrew is found at `/opt/homebrew` or `/usr/local` even when it is not on the
  window's PATH yet, and the line Homebrew's "Next steps" ask for is added to `~/.zprofile` once.
  `engine.probe.find_tool` also looks in those two folders on macOS, so Plan B works from any window.
- **Verified:** a fresh clone run with PATH limited to the macOS system folders, using stand-ins for
  the Homebrew installer and `brew install`: answering no (and no terminal) stops without installing;
  answering yes installs, finishes setup, and a helper started from such a window passes the 14-clip
  smoke 7/7. **Not verified:** the real Homebrew installer on a Mac without Homebrew.

## D-024 Panel 0.1.1: the panel scrolls; a selected bin counts as its clips

- **Evidence:** the first Mac tester's screenshot (Premiere on macOS, 15 Nov 2026): the panel showed
  **Helper connected** (so the HTTPS route of D-022 reaches the helper), but the panel did not
  scroll, and **Use selected project items** failed with "Select clips in the Project panel first".
- **Choice:** `body { height: 100vh; overflow-y: auto; }`, the rule Adobe's Premiere sample panel
  `metadata-handler/styles.css` uses. A selected bin contributes every clip inside it (sub-bins
  included, each file once). When nothing usable is found, the message says how many items Premiere
  reported as selected, which separates "nothing selected" from "selection not readable".
- **Verified:** panel tests against the simulated Premiere (19 pass, one new). **Not verified:**
  scrolling and selection in the real Premiere; the tester's next run checks both.
- The panel version is 0.1.1 so Creative Cloud installs the new `.ccx` over the old one.
