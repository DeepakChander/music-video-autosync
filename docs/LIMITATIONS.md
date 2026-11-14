# Limitations (v0.1.0)

What the product cannot do, or has not proven yet, and the working alternative for each
(spec rule 4). Nothing here is hidden from the user: each case is reported in the panel.

## Not yet proven in a real Premiere Pro

| Item | Status | Alternative / how it gets proven |
|---|---|---|
| Placement inside Premiere (both methods) | Built from Adobe's UXP reference and official samples (`docs/PREMIERE_API_NOTES.md`); tested against a simulated Premiere (`panel/test/`). The development machine has no Premiere Pro | The panel's **Placement self-test** runs the spec's Phase 0 spikes on the client's Premiere and reads every clip back. `docs/CLIENT_SMOKE_TEST.md` |
| Confidence thresholds | Calibrated on synthetic takes only (`docs/DECISIONS.md` D-008) | Real 30-take set with a truth file (`docs/real_truth_template.csv`) and `tools/accuracy_report.py --gate real` |
| Analysis speed on a Windows laptop | Measured on Apple M5, 10 cores: 200 takes (19 s average) in ~15 s | The self-test full run (200 clips) reports times on the client machine |

## Premiere API limits (UXP, Premiere 26.x)

| Limit | Effect | Working alternative |
|---|---|---|
| No API to add tracks; only *insert* is documented to create one | The UXP method creates each new track by inserting the clip far beyond the end of the song (nothing there to ripple) and moving it to its position with delta moves, video and scratch audio separately | XML method defines all tracks in the file |
| Moves are documented as "shifting by" a time; negative deltas are not explicitly documented | Staged clips move left by a negative delta | Readback catches any failure; the self-test shows it; XML method avoids moves |
| No video-only or audio-only insert | With **Keep scratch audio** off, the UXP method still places the camera audio, mutes it and says so | XML method leaves it out |
| `importFiles` creating a sequence from FCP7 XML is undocumented in UXP | The XML method may get no sequence back | The panel says so; File > Import the XML, open it, click **Verify imported sequence** (same mute + readback) |
| Track mute is a direct call (`setMute`), not an undoable action | Undo does not unmute scratch tracks | Un-mute by hand in the track header |
| Insert/overwrite ignore source in/out set in the same transaction (third-party evidence) | In/out marks are set in their own transaction first and the user's original marks restored after | - |
| Network permission: Premiere rejects `http://127.0.0.1:<port>` entries (third-party evidence) | The manifest uses the documented `"domains": "all"`; the panel only contacts 127.0.0.1 | - |
| Setting a new sequence's frame rate needs 26.2+ | On 26.0-26.1 the UXP method stops with a clear message | XML method |
| No API to read clip links | Linked audio is found by its project item and start time | - |
| Moving a corrected clip to a different track in an existing draft | Not done in place; the correction is saved | Generate a new draft (corrections are kept as overrides) |

## Sync limits (by design or v1 scope)

| Limit | Effect | Alternative |
|---|---|---|
| Takes with no audio, silent audio, < 2 s of audio, or audio FFmpeg cannot decode (some RAW formats) | UNSYNCED with reason; never placed | Place by hand (type a position in Review) |
| Audio that repeats exactly in the song (choruses, copied hooks) | A take lying only in repeated audio cannot be told apart: LOW / AMBIGUOUS, every candidate listed, not placed | Pick the right candidate with **Use ...** |
| Short takes (2-6 s) | No window validation possible: MEDIUM at most | Check them in Review |
| Positions follow the camera microphone | Speaker-to-camera distance delays the scratch audio by ~2.9 ms per metre (10 m = ~29 ms, under one frame at 24 fps). Not corrected | Nudge by a frame if needed |
| Very live rooms (direct sound much quieter than the echo, below about -15 dB) | The sample-level pass can lock onto a reflection (up to ~15 ms late) | Nudge by a frame if needed |
| Slow-motion takes (2x playback) | Detected and placed at their start at native speed; the engine never conforms footage. Tracks reserve the conformed length | Interpret Footage to the sequence rate in Premiere |
| Clock drift | Reported per clip (ppm), never corrected; shown as unreliable when the fit is poor | Speed adjust by hand if a long take drifts more than a frame |
| One master song per sequence | - | - |
| LOW clips are not placed by default | They wait in AutoSync / Needs review | Tick **Also place low-confidence (LOW) clips**, or pick a candidate |
| The **Frame-rate conversion** setting is disabled | Out of scope for v1 | - |

## macOS (run from source, docs/MAC_GUIDE.md)

| Limit | Effect | Working alternative |
|---|---|---|
| No Mac installer; no tray app | Clone the repository, run `scripts/setup_mac.sh`, keep `scripts/start_helper_mac.sh` running in a Terminal window | - |
| Premiere disallows `http://` URLs on macOS (Adobe's Premiere UXP network recipe) | The helper serves HTTPS with a certificate from a local mkcert authority (`scripts/setup_mac_https.sh`); the panel tries http, then https. Whether Premiere accepts that certificate is **not verified** | Plan B: `engine.cli analyze` + `engine.cli report` + `python -m export.fcp7_xml`, then File > Import in Premiere |
| The panel inside Premiere on a Mac | **Not verified** (no Premiere on the build machine) | The panel's placement self-test checks it on the Mac |

## Distribution limits

- Unsigned build: Windows SmartScreen and Creative Cloud show warnings on install.
- The installer installs the panel with Adobe's `UnifiedPluginInstallerAgent` when Creative Cloud is
  present. That tool reports nothing back; if the panel is missing, double-click the `.ccx`.
- The panel talks to port 47653; changing the helper port also needs a panel change in v1.
