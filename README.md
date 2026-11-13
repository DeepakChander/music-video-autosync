# Music Video AutoSync

Aligns 100-200 music video takes to one master song and builds an editable first-draft
Premiere Pro sequence. Every clip with usable reference audio is placed at its song position
(within 1 frame); every clip without one is reported UNSYNCED with a reason. No position is
ever guessed.

It is signal processing (MFCC cross-correlation + GCC-PHAT), not machine learning.

## Components

| Folder | What | Runs |
|---|---|---|
| `engine/` | Sync engine: decode, features, locate, validate, confidence, layout, `sync_result.json` | Pure Python, no Premiere, no network |
| `helper/` | Local FastAPI service on 127.0.0.1: jobs, progress, cancel, cache, token, overrides, XML export | Windows tray app |
| `export/` | FCP7 XML (xmeml v5) writer for placement adapter B | Called by the helper |
| `panel/` | Premiere Pro UXP panel: UI, placement adapters A (UXP) and B (XML), readback verification | Inside Premiere |
| `tools/` | Synthetic take generator, accuracy report, threshold calibration | Development / acceptance |
| `docs/` | INSTALL, USER_GUIDE, LIMITATIONS, DECISIONS, LICENSES, PREMIERE_API_NOTES, reports | |

`sync_result.json` (schema in `schema/`) is the single source of truth. Only the engine writes
sync positions; manual corrections live in each clip's `override`.

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
