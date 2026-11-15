# Ad first cut from a script or storyboard

Section **7. Ad first cut** in the panel (or `autosync-engine ad` on the command line) builds a
commercial's first cut (assembly): every shot of the script in order, each with its best take,
trimmed to the line, to a target length (6 / 15 / 30 / 60 s) if you want one. Missing shots are
marked, never filled with a guess. No master song is needed.

## What you give it

| Input | Required | Formats |
|---|---|---|
| Script / storyboard / shot list | yes | `.csv` or `.xlsx` shot list (template below), `.docx` AV script (two-column VIDEO / AUDIO table), `.fdx` (Final Draft), `.txt`, storyboard `.pdf` |
| Footage | yes | the clips added in section 2 (any format FFmpeg reads) |
| Take log | no | CSV: `clip, shot, take, circled, notes` (script supervisor / camera report). Circled takes win |
| Music bed | no | any audio file; cuts of picture-only shots move onto its beats |
| VO recording | no | any audio file; each VO shot lasts as long as its line and gets that piece of the VO |

The panel reads the script as soon as you select it and shows the shots it found (and anything it
had to guess about the layout) before you build anything.

### Shot-list template

**Save shot-list template…** writes this CSV (Excel opens it):

| order | shot | type | description | dialogue | speaker | duration | board | super | notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1A | mos | Wide: kitchen at sunrise | | | 3 | | | |
| 2 | 2A | dialogue | Close: Sarah at the coffee maker | Fresh coffee in sixty seconds. | SARAH | | | | |
| 3 | 3A | mos | Insert: coffee pouring | | | 2 | | | |
| 4 | 4A | vo | Packshot on the counter | Aurora. Wake up better. | VO | 3 | | AURORA | |

- **type**: `dialogue` (spoken on camera), `vo` (voice-over, picture is MOS), `mos` (picture only),
  `graphic` (end card, super). Left empty: dialogue if there is a line, VO if the speaker is VO.
- **duration**: seconds, for shots without dialogue (default 2.5 s).
- **board**: a storyboard image file for the shot (optional).
- Header names are flexible: "Shot #", "Video", "Audio", "VO", "Dur" and similar are recognised.

## How takes are found (in this order)

1. **Take log**: the clip is logged as that shot. Circled takes come first.
2. **File name**: the shot and take are in the name: `03A_T2`, `SH03A-TK02`, `shot 12 take 3`,
   `S03_SH02_T04`, `3A-2`. Camera names like `A001C004` carry no shot: use the take log or dialogue.
3. **Dialogue** (dialogue shots): every clip's sound is transcribed on this computer (whisper.cpp,
   nothing is uploaded) and each scripted line is searched for in the transcripts. A match needs 70%
   word similarity; numbers are compared as words ("60" = "sixty"). The take is trimmed to the line
   (0.25 s before the first word, 0.4 s after the last), using the measured speech in the sound,
   because the speech model's own word times run into the silence around a line. The first run
   downloads the speech model (148 MB) once.
4. **Storyboard picture** (picture-only shots with a board image, no take found otherwise): the
   frame that looks most like the board picture, as a **suggestion** (yellow "CHECK" marker).

When several takes match: circled first, then the best line delivery, then a logged/named match,
then the later take, then picture quality (if shots were analysed).

## What you get

- Sequence **AutoSync Ad - <script> - <length>** opened in Premiere (or the `.xml` path for File >
  Import): V1 picture in script order; A1 the dialogue takes' sound; A2 the VO recording cut per
  line; A3 the music bed. One marker per shot: green (sure), blue (likely), yellow (check),
  red **MISSING** (no take; the gap keeps the shot's length).
- With a target length, the picture-only shots are lengthened or shortened (1 s minimum) so the cut
  lands on the length **to the frame**. Spoken lines and recorded VO are never cut; if they alone do
  not fit, the panel says by how much.
- **Render ad preview** makes an MP4 with picture, dialogue, VO and music (music at -12 dB).

## What has been checked

| Check | Result |
|---|---|
| Script formats: CSV, XLSX, DOCX AV table, FDX, TXT, storyboard PDF with pictures | Unit tests (`tests/unit/test_ad.py`) |
| File names, take log, circled takes, missing shots, script order, target length to the frame, lines never cut, beat snapping | Unit tests |
| Dialogue matching with real speech | Simulated shoot on macOS (spoken lines made with the Mac's `say`), tiny.en model: both lines found (100%), the fumbled take skipped, the circled take used, lines trimmed to the measured speech, 15 s spot exact |
| Helper jobs, preview video with mixed sound | `tests/helper/test_ad_api.py`, helper smoke test 11/11 |
| Real shoot footage, real actors, location sound | **Not checked yet** |
| Premiere importing the ad XML (markers, linked sound) | **Not checked yet** (same route as the music video cut) |

## Command line

```bash
autosync-engine ad --template shotlist.csv                     # write the template
autosync-engine ad --script script.docx --clips ./footage --fps 25 --target 30 \
  --takelog takelog.csv --music bed.wav --vo vo.wav --out ad.xml --preview ad.mp4
```

`--model tiny.en | base.en | small.en` (default base.en), `--no-transcribe` (take log and file
names only), `--analyse-shots` (pick the best-looking part of picture-only takes).
