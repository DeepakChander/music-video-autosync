# Music Video AutoSync

Aligns 100-200 music video takes to one master song and builds an editable first-draft
Premiere Pro sequence. Every clip with usable reference audio is placed at its song position
(within 1 frame); every clip without one is reported UNSYNCED with a reason. No position is
ever guessed.

Then it edits: the **first cut** cuts between the synced takes on the beat of the song, with a new
shot at every section, pacing by style (performance, performance + story, hype, ballad), story
footage in verses and breaks, section markers, optional punch-ins/flashes/dissolves and a preview
video (docs/EDIT_DESIGN.md).

It is signal processing (MFCC cross-correlation + GCC-PHAT), not machine learning.

## Components

| Folder | What | Runs |
|---|---|---|
| `engine/` | Sync engine: decode, features, locate, validate, confidence, layout, `sync_result.json` | Pure Python, no Premiere, no network |
| `helper/` | Local FastAPI service on 127.0.0.1: jobs, progress, cancel, cache, token, overrides, XML export | Windows tray app |
| `export/` | FCP7 XML (xmeml v5) writers: synced draft and first cut; preview MP4 renderer | Called by the helper |
| `panel/` | Premiere Pro UXP panel: UI, placement adapters A (UXP) and B (XML), readback verification | Inside Premiere |
| `tools/` | Synthetic take generator, accuracy report, threshold calibration | Development / acceptance |
| `docs/` | INSTALL, USER_GUIDE, LIMITATIONS, DECISIONS, LICENSES, PREMIERE_API_NOTES, reports | |

`sync_result.json` (schema in `schema/`) is the single source of truth. Only the engine writes
sync positions; manual corrections live in each clip's `override`.

## Install

- **Windows:** the installer from the GitHub Release, then `docs/CLIENT_SMOKE_TEST.md`.
- **Mac:** clone this repository and follow `docs/MAC_GUIDE.md` (`./scripts/setup_mac.sh`,
  `./scripts/setup_mac_https.sh`, `./scripts/start_helper_mac.sh`). Includes a command-line route
  that needs no panel.

## Engine quick start (no Premiere needed)

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # Python 3.11, FFmpeg on PATH
.venv/bin/python -m engine.cli analyze --master song.wav --clips ./footage --fps 24000/1001 --out sync_result.json
```

Prove accuracy against known answers:

```bash
.venv/bin/python tools/make_synthetic_takes.py --out ./synthetic --count 60 --seed 1
.venv/bin/python -m engine.cli analyze --master synthetic/master/song_master.wav --clips synthetic/takes --fps 24000/1001 --out synthetic/result.json
.venv/bin/python tools/accuracy_report.py --truth synthetic/truth.csv --result synthetic/result.json --gate synthetic
```

Tests: `.venv/bin/python -m pytest` (FFmpeg required for the end-to-end tests).

## Status

| Phase | Gate | Evidence |
|---|---|---|
| 0 Spikes | Run on the client's Premiere by the panel's **Placement self-test** (no Premiere on the dev machine) | `docs/CLIENT_SMOKE_TEST.md` |
| 1 Engine | Synthetic: PASS on two 200-take holdouts. Real 30-take set: pending client truth file | `docs/reports/phase1_*` |
| 2 Helper | PASS: 200-clip job, kill + resume, all endpoints tested | `docs/reports/phase2_helper.md` |
| 3 Placement | Logic tested against a simulated Premiere; real readback via the self-test | `panel/test/` |
| 4 Panel UI | Every button wired to a real operation (tested) | `panel/test/ui.test.js` |
| 5 Packaging | PASS on a Windows runner: build, silent install, installed-helper end-to-end smoke (7/7), silent uninstall leaves nothing. Panel install via Adobe UPIA not testable there | `docs/reports/phase5_packaging.md` |
| 6 Acceptance | Client's real music video project | `docs/CLIENT_SMOKE_TEST.md` |

## Docs

`docs/CLIENT_SMOKE_TEST.md` (Windows, start here) · `docs/MAC_GUIDE.md` (Mac) · `docs/INSTALL.md` · `docs/USER_GUIDE.md` ·
`docs/LIMITATIONS.md` · `docs/DECISIONS.md` · `docs/LICENSES.md` · `docs/PREMIERE_API_NOTES.md`
