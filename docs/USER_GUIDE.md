# User guide

Music Video AutoSync builds a technically accurate rough-cut timeline: every take with usable
scratch audio is placed at its song position, every other take is listed with a reason. It does
not choose takes, cut on beats, add transitions or effects, grade, trim clips or touch the master
audio, your footage or your existing sequences.

## The panel, top to bottom

**Header.** The light shows the helper: green connected, amber needs pairing or FFmpeg missing, red
not running. When it is red, nothing is analysed or placed.

**1. Master audio.** **Select master...** picks the song (WAV, AIFF, MP3, AAC/M4A, FLAC).
**Analyse Music** decodes it once and shows its waveform and length. The analysis is cached.

**2. Footage.**
- **Import files...** picks takes on disk (multi-select).
- **Use selected project items** takes the clips selected in the Project panel.
- The panel shows the clip count, the total length, and how many clips have no audio or cannot be
  read.
- **Analyse All Clips** starts the analysis in the helper; Premiere stays usable meanwhile.

**3. Sync settings.**

| Setting | Default | Meaning |
|---|---|---|
| Audio matching | on (fixed) | Positions come only from audio, never from timecode or recording time |
| Preserve footage | on (fixed) | Source files are never modified, conformed or re-encoded |
| Keep scratch audio | on | Each take's camera audio goes on its own track under the song |
| Mute scratch audio | on | Those tracks are muted |
| Separate video tracks | on (fixed) | Overlapping takes go on separate tracks, never overwrite |
| Frame-rate conversion | off (not in v1) | |
| Also place low-confidence (LOW) clips | off | See statuses below |
| Search 2x playback | on | Finds slow-motion takes shot against sped-up playback |
| Sequence | 23.976, 3840x2160 | Frame rate and size of the new sequence; positions snap to its frames |
| Placement | Self-test default | UXP direct, FCP7 XML import, or whichever passed the self-test |

**4. Progress.** Shows analysed / synced / unsynced / low-confidence counts and the current clip.
**Cancel** stops after the clips in progress; finished clips are kept. If the helper stops
mid-run, **Resume** continues from its cache.

**5. Generate.**
- **GENERATE FIRST DRAFT** creates a new sequence named `AutoSync - <song> - <date time>`:
  - the song is on A1 at 00:00:00:00;
  - each placed take sits at its song position on its own video track;
  - its camera audio is on a muted track below the song.
- It then reads every clip back from Premiere. **Draft ready and verified** appears only when every
  position matched; otherwise every mismatch is listed.
- **Open Sequence** brings the draft back.

## Statuses

| Status | Meaning | Placed by default |
|---|---|---|
| HIGH | Strong match, one clear position, every validation window agrees, sample-accurate | yes |
| MEDIUM | Good match with a little less evidence (or a take shorter than 6 s) | yes |
| LOW | Weak evidence, or AMBIGUOUS: the take's audio occurs at several song positions (choruses). All candidates are listed | no, goes to AutoSync / Needs review |
| UNSYNCED | No usable match. Reasons: NO_AUDIO (no or silent audio), TOO_SHORT (< 2 s), NO_MATCH (audio not from this song), DECODE_ERROR (file unreadable), NOT_ANALYZED (run cancelled) | never, goes to AutoSync / Unsynced |

A take that starts before the song (pre-roll) is placed at 00:00:00:00 with its in-point moved so
the song lines up; the head stays available as a handle.

## Review list

Every take is listed with its status, position, reason and evidence (score, peak ratio, window
agreement, playback rate, drift).
- **Use hh:mm:ss:ff (score)** chooses one of the alternate positions.
- **-1f / +1f** nudges by one sequence frame.
- Type a timecode, `m:ss.sss` or seconds, then **Set**.
- **Clear** removes your correction.
- **Preview** moves the playhead to the take in the draft.

Corrections are saved as overrides next to the analysis (the original evidence is kept). The take
then moves in the draft and is read back. If the new position needs another track, generate a new
draft.

## Placement self-test

Generates takes with known answers, analyses them, and places them with both methods into new
sequences. Every clip is read back. The method that passes becomes the default. Run it once
per machine and again after a Premiere update. Use a scratch project.

## Files and folders

| What | Where |
|---|---|
| Program | `%LOCALAPPDATA%\Programs\MusicVideoAutoSync` |
| Token, config, logs | `%APPDATA%\MusicVideoAutoSync` |
| Analysis cache, jobs, results, reports, XML | `%LOCALAPPDATA%\MusicVideoAutoSync\cache` (tray > Open cache folder) |
| Result of a run | `cache\jobs\<id>\sync_result.json` (schema in `schema/sync_result.schema.json`) |

Nothing is uploaded: the helper only listens on 127.0.0.1 and makes no outbound requests.
