# Music Video AutoSync on a Mac (run from the Git repository)

You clone the project, run one setup script (it installs anything missing), start the helper in
a Terminal window, and use the panel inside Premiere Pro. If the panel cannot connect on your Mac,
a command-line route (Plan B below) does the same sync and gives you a sequence to import with
File > Import.

## What has been checked on a Mac, and what has not

| | Status |
|---|---|
| Fresh clone, `setup_mac.sh`, helper start (http and https), self-test analysis: 100% of test takes within 1 frame | Checked on macOS (Apple Silicon, Python 3.11) |
| `setup_mac.sh` on a Mac with no Homebrew, FFmpeg or Python: offers Homebrew, installs FFmpeg and Python 3.11, finishes | Checked with stand-ins for the Homebrew installer and `brew install`; the real Homebrew installer runs on your Mac |
| Plan B: analyse, report, XML export from the command line | Checked on macOS |
| Placing clips with the panel in Premiere on a Mac | **Not checked yet**. Step 8 checks it on your Mac |
| The panel in Premiere on a Mac reaching the helper | **Checked** on a tester's Mac (15 Nov 2026): the panel showed **Helper connected** after steps 3 and 4. Premiere blocks plain `http://` on macOS (Adobe's Premiere UXP network guide), so the helper uses HTTPS |
| Premiere importing the XML from Plan B | **Not checked** in Premiere yet (it is the standard Final Cut Pro 7 XML format) |

Please send back what you see in steps 5 and 8, whether or not it works.

## You need

- A Mac (Apple Silicon or Intel) with a recent macOS, an internet connection, and your Mac
  password (installing Homebrew needs an administrator account)
- Adobe Premiere Pro **26.2 or later** and the Creative Cloud app
- Access to the private GitHub repository `DeepakChander/music-video-autosync`:
  send Deepak your GitHub username, then accept the invitation email

You do not need to install FFmpeg, Python or Homebrew yourself: step 2 does it.

## Step 1. Get the code

Open **Terminal** (Applications > Utilities) and run:

```bash
cd ~/Desktop
git clone https://github.com/DeepakChander/music-video-autosync.git
cd music-video-autosync
```

The first time you use `git`, macOS may offer to install the Command Line Tools: click
**Install**, wait, then run the commands again.

## Step 2. Set up (once, 5 to 20 minutes)

```bash
./scripts/setup_mac.sh
```

What it does, in order:

1. Checks for FFmpeg and Python 3.11. If either is missing it uses Homebrew, the standard Mac
   package manager, to install it.
2. If Homebrew is not installed, it asks **Install Homebrew now? [Y/n]**. Press **Return**.
   Homebrew asks for your Mac password (nothing appears while you type; that is normal) and asks
   you to press **Return** again. It can take 5 to 15 minutes the first time.
3. Installs the Python packages into a private `.venv` folder, creates the pairing token and
   builds the panel file.

It ends with **Setup finished.** If it stops with a message, do what the message says and run
`./scripts/setup_mac.sh` again: it is safe to run again and skips what is already done.

## Step 3. Turn on HTTPS for the panel (once)

```bash
./scripts/setup_mac_https.sh
```

macOS asks for your password once. The script installs `mkcert`, creates a small certificate
authority that exists only on your Mac, trusts it in your keychain, and makes a certificate that
is valid only for `127.0.0.1` and `localhost`. That lets the Premiere panel talk to the helper
over HTTPS, which Premiere requires on macOS. (Undo: see "Remove everything" at the end.)

## Step 4. Start the helper (every time you use the panel)

```bash
./scripts/start_helper_mac.sh
```

Wait for **Application startup complete.** The first line should show
`https://127.0.0.1:47653`. **Keep this Terminal window open** while you work; Ctrl+C stops the
helper. The helper only listens on your own Mac and never uploads anything.

## Step 5. Check the helper (1 minute, optional but useful)

Open a second Terminal tab (Cmd+T), go to the same folder, and run:

```bash
cd ~/Desktop/music-video-autosync
.venv/bin/python tools/helper_smoke.py --count 14
```

Expected: **11/11 checks passed**. This proves the analysis, the shot analysis (face detection), the
first cut with colour match and the ad first cut work on your Mac without Premiere.

## Step 6. Install the panel in Premiere

```bash
open packaging/out
```

Double-click **MusicVideoAutoSync_premierepro.ccx**. Creative Cloud warns that the plugin is not
from the Marketplace. Click **Install**, then restart Premiere and open
**Window > UXP Plugins > Music Video AutoSync**.

Developer alternative: Premiere > Settings > Plugins > **Enable developer mode** (restart
Premiere). Then, in the **UXP Developer Tool** from the Creative Cloud app, click **Add Plugin**,
choose `panel/manifest.json` and click **Load**.

## Step 7. Connect

The light at the top right of the panel:

| Light | Do this |
|---|---|
| Green: Helper connected | Nothing, you are ready |
| Amber: Helper needs pairing | In Terminal: `.venv/bin/python -m helper print-token`, copy the line, paste it into the panel, **Save** |
| Red: Helper not running | Is the step 4 window still open and showing `https://`? If it shows `http://`, run step 3 and restart the helper. Still red: use Plan B and tell us |

## Step 8. Quick test inside Premiere (5 minutes)

Create a **new empty project** (the test adds bins and sequences). In the panel, scroll to
**Placement self-test** and click **Run quick test (24 clips)**.

Expected results:

| Line | Expected |
|---|---|
| (a) Panel reaches the helper | PASS |
| Engine accuracy on known takes | PASS, 100% within 1 frame |
| (b) UXP direct placement + readback | PASS, 0 mismatches |
| (c) XML import placement + readback | PASS, or "Premiere did not import the XML via UXP" (acceptable) |

Open the new **AutoSync Self-test ...** sequences: the song is on A1 from the start, the takes
are on V1, V2, V3..., and each take's own audio is on muted tracks below.

## Step 9. Use it on a real music video

Work on a copy of your project (File > Save As...). Then, in the panel, from top to bottom:

1. **Master audio**: **Select master...** (the song), then **Analyse Music**.
2. **Footage**: **Import files...** (select many takes), or click your clips, or the bin that holds
   them, in Premiere's Project panel and then click **Use selected project items**.
3. **Sync settings**: set **Sequence** to your timeline's frame rate and size.
4. **Analyse All Clips**, and watch Progress.
5. **GENERATE FIRST DRAFT**. **Draft ready and verified** means Premiere confirmed every clip's
   position. Then **Open Sequence**.
6. **Review**: fix takes with **Use ...** (alternate positions), **-1f / +1f**, or type a
   timecode and click **Set**.

7. **First cut (edit)**: first click **Analyse shots** (faces, close/medium/wide framing,
   sharpness, exposure, camera setups, colour; about 3 s per minute of HD footage). Then choose a
   **Style** (Performance, Performance + story, Hype, Ballad), **Colour match** and a **Look** if you
   want them, a **Pace** and **Effects**, then **MAKE FIRST CUT**. A new sequence "AutoSync Cut - ..." opens: the
   takes are cut on the beat with a marker at every song section, the song on A1. **Another
   variation** makes a different cut with the same rules; **Render preview video** writes an MP4 of
   the cut (and adds it to bin AutoSync) that you can watch or send without Premiere. If the panel
   says Premiere did not import the cut, use File > Import with the .xml path it shows.
   Colour match writes one LUT file per take (the panel shows the folder). Premiere does not let a
   panel load a LUT, so in Lumetri Color > Basic Correction > Input LUT > Browse, choose the take's
   LUT (each clip's comment names it); the preview video already has the colour applied.

8. **Ad first cut** (commercials, section 7): **Select script…** (.csv, .xlsx, .docx, .fdx, .txt or
   storyboard .pdf; the panel lists the shots it read), add the footage in section 2, optionally a
   **Take log**, **Music bed** and **VO recording**, choose a **Length**, then **MAKE AD FIRST CUT**.
   The first time, the speech model (148 MB) downloads once. **Save shot-list template…** writes
   a template to fill in. Full guide: `docs/AD_GUIDE.md`.

| Status | Meaning | On the timeline |
|---|---|---|
| HIGH | Strong, clear match | Yes |
| MEDIUM | Good match, a little less evidence | Yes |
| LOW | Weak, or the audio repeats in the song (chorus); all candidates listed | No, it goes to bin AutoSync / Needs review |
| UNSYNCED | No audio, too short, another song, or unreadable; reason shown | No, it goes to bin AutoSync / Unsynced |

## Plan B: no panel, just Terminal and File > Import

Use this if the panel cannot connect, or if you prefer the command line. The helper does not
need to be running.

```bash
cd ~/Desktop/music-video-autosync

# 1. Analyse: the song, then a folder of takes (or individual files)
.venv/bin/python -m engine.cli analyze \
  --master "/path/to/song.wav" \
  --clips "/path/to/takes" \
  --fps 24000/1001 \
  --out ~/Desktop/sync_result.json

# 2. Read the result: every take with status, position, track and reason
.venv/bin/python -m engine.cli report --result ~/Desktop/sync_result.json

# 3. Make the sequence for Premiere
.venv/bin/python -m export.fcp7_xml \
  --result ~/Desktop/sync_result.json \
  --out ~/Desktop/AutoSync_draft.xml
```

Then, in Premiere: **File > Import**, choose `AutoSync_draft.xml`, and open the new sequence.
Mute the camera-audio tracks (A2 and up) with their track headers.

First cut from the command line (an edited sequence plus a preview video):

```bash
.venv/bin/python -m engine.cli cut --result ~/Desktop/sync_result.json \
  --style performance --out ~/Desktop/AutoSync_cut.xml --preview ~/Desktop/AutoSync_cut.mp4
```

`--style` is `performance`, `performance_story`, `hype` or `ballad`; `--pace 1.4` cuts faster,
`--seed 2` gives another variation; `--analyse-shots` looks at the pictures first (faces, framing,
sharpness); `--colour` matches every take to a hero take and `--look warm` (cool, teal_orange,
bw_contrast, film_fade) adds a look, both as .cube LUTs in a `luts` folder next to the .xml.
File > Import the .xml as above; the .mp4 plays anywhere.

Ad first cut from the command line:

```bash
.venv/bin/python -m engine.cli ad --script ~/Desktop/script.docx --clips ~/Desktop/footage \
  --fps 25 --target 30 --out ~/Desktop/ad.xml --preview ~/Desktop/ad.mp4
```

Add `--takelog log.csv`, `--music bed.wav`, `--vo vo.wav` when you have them;
`.venv/bin/python -m engine.cli ad --template ~/Desktop/shotlist.csv` writes the template.

| Your timeline | `--fps` value |
|---|---|
| 23.976 | `24000/1001` |
| 24 / 25 / 30 / 50 / 60 | `24` / `25` / `30` / `50` / `60` |
| 29.97 | `30000/1001` |
| 59.94 | `60000/1001` |

Options for command 3 (the XML): `--place-low` also places LOW takes; `--no-scratch` leaves the camera audio
out. Drag a folder from Finder into Terminal to type its path.

## Updating to a newer version

```bash
cd ~/Desktop/music-video-autosync
git pull
./scripts/setup_mac.sh
```

If the panel changed, install the new `.ccx` again (step 6).

## Troubleshooting

| Problem | Fix |
|---|---|
| `git clone` says "Repository not found" | Your GitHub account has no access yet: send Deepak your username and accept GitHub's invitation email |
| `git clone` asks for a password | GitHub wants a personal access token, not your account password (github.com > Settings > Developer settings > Personal access tokens) |
| `Setup stopped: FFmpeg is not installed` (an older copy of the script) | `git pull`, then `./scripts/setup_mac.sh` again: it now installs FFmpeg and Python itself |
| Homebrew says you need `sudo` access | Your Mac account is not an administrator. Log in as an administrator, or ask the Mac's owner to run step 2 |
| `zsh: command not found: brew` in a new window | Close Terminal and open it again (setup added Homebrew to `~/.zprofile`). The AutoSync scripts find Homebrew either way |
| `permission denied: ./scripts/...` | `chmod +x scripts/*.sh` |
| `port 47653 is already in use` | A helper is already running in another Terminal window; use it or close it |
| The panel does not scroll, **Use selected project items** ignores a selected bin, or there is no **First cut** / **Ad first cut** section | An older panel. Update (see "Updating to a newer version") and install the new `.ccx`; the panel's About line shows **Panel 0.4.0** or later |
| `helper_smoke.py` says the helper is not reachable | Start it first (step 4) and wait for "Application startup complete." |
| The panel shows red, but the helper runs with `https://` | Premiere may not accept the local certificate. Use Plan B, and send us a screenshot plus the panel's error from UXP Developer Tool > Debug |
| A take is UNSYNCED / DECODE_ERROR | FFmpeg cannot read its audio (some RAW formats). Place it by hand in Review (type a timecode, click **Set**) |

## What to send back

- The output of step 5 (copy it from Terminal)
- Screenshots of the panel's self-test results (step 8) and of a draft timeline
- The helper's Terminal window text if anything failed, or the log at
  `~/Library/Application Support/MusicVideoAutoSync/logs/helper.log`
- For Plan B: `sync_result.json` and the report text

## Where things are on a Mac

| What | Where |
|---|---|
| The code | the folder you cloned (for example `~/Desktop/music-video-autosync`) |
| Token, settings, certificate, logs | `~/Library/Application Support/MusicVideoAutoSync` |
| Analysis cache, jobs, results, reports | `~/Library/Caches/MusicVideoAutoSync/cache` |

## Remove everything

1. Remove the panel in the Creative Cloud app's plugin management, or unload it in the UXP
   Developer Tool.
2. Delete the cloned folder.
3. Delete the data: `rm -rf ~/Library/Application\ Support/MusicVideoAutoSync ~/Library/Caches/MusicVideoAutoSync`
4. Remove the local certificate authority: `mkcert -uninstall`

Homebrew, FFmpeg, Python and mkcert stay installed (other apps can use them). To remove them too:
`brew uninstall ffmpeg python@3.11 mkcert`.

Your footage, songs and Premiere projects are never changed by any of this.
