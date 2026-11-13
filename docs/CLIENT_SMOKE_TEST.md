# Client smoke test: Music Video AutoSync in Premiere Pro

This is the first test to run on your machine. It takes about 20 minutes. Test 1 needs no
footage of your own; test 2 uses a real shoot.

**Why it matters.** The engine and the helper have been tested automatically, including a full
install / run / uninstall on a Windows test machine. The placement inside Premiere has **not** been
run in a real Premiere Pro yet: it was built from Adobe's documentation and tested against a
simulated Premiere. Test 1 below is the check that it works in your Premiere.

## What you need

| | |
|---|---|
| Computer | Windows 10 or 11, 64-bit, 8 GB RAM or more |
| Premiere Pro | 26.2 or later (Help > About Premiere Pro). 26.0-26.1 can use the XML method only |
| Creative Cloud desktop app | Installed and signed in (it installs the panel) |
| Installer | `MusicVideoAutoSync-Setup-0.1.0.exe` (sent to you) |
| Disk | About 1 GB free on C: |

## 1. Install (2 minutes)

1. Close Premiere Pro.
2. Double-click `MusicVideoAutoSync-Setup-0.1.0.exe`. It installs for your user only and needs no
   admin rights.
   - Windows may show "Windows protected your PC" because this test build is not code-signed.
     Click **More info**, then **Run anyway**.
3. Leave **Start the AutoSync helper now** ticked and click **Finish**.
4. A waveform icon appears in the Windows tray (bottom right; it may be under the **^** arrow).
   Right-click it: the first line must read **Helper running on 127.0.0.1:47653**.

The installer also installs the Premiere panel through Adobe's plugin installer, starts the helper
with Windows, and creates the pairing token the panel uses to talk to the helper.

## 2. Open the panel in Premiere (1 minute)

1. Start Premiere Pro and open a **new, empty test project** (File > New > Project). Test 1 adds
   bins and sequences, so do not use a real project for it.
2. Open **Window > UXP Plugins > Music Video AutoSync** and dock the panel.
3. If the panel is **not** in that menu, install it by hand:
   - Start menu > **Music Video AutoSync - Premiere panel (.ccx)** opens a folder.
   - Double-click `MusicVideoAutoSync_premierepro.ccx`. Creative Cloud warns that the plugin is not
     from the Marketplace. Click **Install**, then restart Premiere.

## 3. Connect the panel to the helper

The top right of the panel shows the connection:

| Light | Meaning | Do this |
|---|---|---|
| Green: Helper connected | Ready | Nothing |
| Amber: Helper needs pairing | The panel could not read the token | Right-click the tray icon > **Copy pairing token**, paste it into the panel's box, click **Save** |
| Red: Helper not running | The helper is closed | Start menu > **Music Video AutoSync Helper** |
| Amber: FFmpeg missing | Broken install | Reinstall |

## 4. Test 1: placement self-test (no footage needed, 3 to 5 minutes)

1. In the panel, scroll to **Placement self-test** and click **Run quick test (24 clips)**.
2. The helper creates 24 test takes from a test song: room noise, slow motion, choruses, b-roll
   without audio, a corrupt file. It knows the right answer for each. It then analyses them, and
   the panel places them twice, once per placement method, each into a new sequence. Every clip is
   read back from Premiere.
3. **Expected result:**

   | Line | Expected |
   |---|---|
   | (a) Panel reaches the helper | PASS |
   | Engine accuracy on known takes | PASS, 100% within 1 frame |
   | (b) UXP direct placement + readback | PASS, 0 mismatches |
   | (c) XML import placement + readback | PASS, **or** "Premiere did not import the XML via UXP" (acceptable: the manual XML route still works) |
   | Default placement method | UXP direct or FCP7 XML import |

4. Look at the two new sequences (**AutoSync Self-test UXP - ...** and **... XML - ...**):
   - The song is on **A1**, starting at 00:00:00:00.
   - The takes are on **V1, V2, ...**, overlapping takes on separate tracks.
   - Each take's own camera audio is on the track below the song (A2, A3, ...), and those tracks
     are muted.
   - Play any take with its scratch track un-muted: the test tone and the song line up.
   - **AutoSync > Unsynced** holds the no-audio, unrelated-song and too-short takes.
   - **AutoSync > Needs review** holds the chorus takes that match several places.
5. Optional, 10 to 15 minutes: **Run full test (200 clips)**. This measures how Premiere behaves with a
   full-size job. Note the **longest UI block** value shown for (b).

**If a line says FAIL:** the panel shows the reason, and the full report is saved in the file
shown after "Report saved:". Send that file (see section 7).

## 5. Test 2: a real shoot (10 minutes)

Use a copy of a real project: File > **Save As...** under a new name.

1. **1. Master audio**: **Select master...**, choose the song file, then **Analyse Music**. The
   waveform and duration appear.
2. **2. Footage**: **Import files...** and pick 20 to 30 takes. Alternatively, select the takes in
   the Project panel and click **Use selected project items**. The panel shows the clip count, the
   total length, and how many clips have no audio.
3. **3. Sync settings**: set **Sequence** to your timeline's frame rate and size. Leave everything
   else as it is.
4. Click **Analyse All Clips**. The progress section counts analysed, synced, unsynced and low-confidence clips.
5. Click **5. GENERATE FIRST DRAFT**. The panel shows **Draft ready and verified** if every placed
   clip read back exactly; otherwise it lists every mismatch. Click **Open Sequence**.
6. Check:
   - Pick five synced takes. Solo A1 plus the take's scratch track: the camera audio and the song
     line up (within one frame).
   - Your existing sequences are unchanged.
   - Takes without audio are in **AutoSync > Unsynced** and listed as UNSYNCED / NO_AUDIO in Review.
   - Chorus takes that match more than one place are LOW / AMBIGUOUS in Review, with **Use ...**
     buttons for each candidate position.
7. Try the **Review** list:
   - **-1f / +1f** nudges a clip one frame.
   - Type a timecode, then click **Set** to move a clip.
   - **Use ...** picks an alternate position.
   - **Preview** jumps the playhead to the clip.

   Each correction is saved as an override (the original analysis is kept), and the clip moves in
   the draft.

## 6. Help us verify accuracy (the "real 30-take" check)

The build spec requires at least 90% of real takes to land within 1 frame, and no HIGH or MEDIUM
take to be wrong. To measure this we need your answers for one real song:

1. Copy `docs/real_truth_template.csv`.
2. Add one row per take:
   - `true_timecode`: the timeline position where you would sync that take by hand (on a sequence
     that starts the song at 00:00:00:00).
   - `expected_reason`: write `NO_AUDIO` for takes without sound.
   - `rate`: write `2` for slow-motion takes shot against 2x playback.
3. Send it along with the files below. We run `tools/accuracy_report.py --gate real` and send you
   the report.

## 7. What to send back

| File | Where |
|---|---|
| Self-test report | Path shown after "Report saved:" in the panel (`...\MusicVideoAutoSync\cache\jobs\<id>\selftest_report.json`) |
| Placement report of test 2 | `%LOCALAPPDATA%\MusicVideoAutoSync\cache\jobs\<id>\placement_report.json` (the most recent folder) |
| Helper log | `%APPDATA%\MusicVideoAutoSync\logs\helper.log` (tray icon > **Open log folder**) |
| Screenshots | The panel's self-test and summary sections, and one draft timeline |
| Truth file | From section 6 |

The jobs folder opens from the tray icon > **Open cache folder**.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Panel missing from Window > UXP Plugins | Install the `.ccx` by hand (section 2), restart Premiere |
| "Setting a sequence frame rate needs Premiere Pro 26.2 or later" | Update Premiere, or set Placement to **FCP7 XML import** |
| "Premiere did not import the XML automatically" | File > Import, choose the `.xml` path shown, open the new sequence, then click **Verify imported sequence** |
| "Helper not running" after a reboot | Start menu > Music Video AutoSync Helper; check it is in Task Manager > Startup apps |
| Port 47653 already used by another program | Create `%APPDATA%\MusicVideoAutoSync\config.json` with `{"port": 47700}`. The panel uses 47653 in this build, so tell us instead |
| A clip shows UNSYNCED / DECODE_ERROR | FFmpeg cannot read that file's audio (for example some RAW formats). Sync it by hand |

## Uninstall

Windows Settings > Apps > **Music Video AutoSync** > Uninstall. This removes:
- the helper and the panel;
- the pairing token;
- the analysis cache.

Your footage, songs and Premiere projects are never touched.
