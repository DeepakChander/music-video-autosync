# First cut: design (music video and ad)

Goal: after sync, build an **editable first cut** in Premiere: one timeline that cuts between takes on
the music the way a music video editor would, with section markers, optional effects and a colour
match, so the editor starts from a strong draft instead of a stack of synced clips. The same panel
also builds an **ad first cut** from a script or storyboard (section "Ad mode" below).

What it is not: a finished video. Editors judge performance and story; the tool does the tedious
part (sync, selects, a beat-accurate rough cut, colour balance) and keeps every alternate one click
away. Research sources are linked inline; the full reports are summarised here.

## 1. What music video editors do (research summary)

| Rule | Source |
|---|---|
| Performance pass first, story fills the gaps after | [IMVDb: Ben Guzman](https://imvdb.com/blog/2013/02/editor-profile-ben-guzman), [IMVDb: Chris Rodriguez](https://imvdb.com/blog/2013/03/editor-profile-chris-rodriguez) |
| Most common shot length is ~1 beat in 329 of 548 official videos, ~1 bar in 95; 4-bar patterns are rare | [Prétet, Richard & Peeters, ISMIR 2021](https://arxiv.org/pdf/2108.00970) |
| Mean shot length 2.1 s (most viewed) to 4.2 s (concept videos) | [Sedeño Valdellós et al. 2016](http://www.revistalatinacs.org/071/paper/1098/18en.html) |
| Cuts on the bar's accented first beat are rated more pleasant than cuts just under half a bar off | [Lin, Yeh & Shams 2022](https://escholarship.org/uc/item/1rq255xj) |
| Choruses faster than verses, acceleration towards climaxes, bridges slower | ISMIR 2021 (editor interview), [Sound On Sound](https://www.soundonsound.com/node/4905278) |
| A new shot on the first downbeat of every chorus | [High On Films](https://www.highonfilms.com/judge-an-ai-music-video-generator-by-section-cuts/) |
| Never the same take/angle twice in a row; 30-degree rule; alternate wide and close | [StudioBinder](https://www.studiobinder.com/blog/what-is-continuity-editing-in-film/), Sound On Sound |
| Do not cut on every beat for long stretches (predictable) | [Adorama](https://www.adorama.com/alc/editing-cuts-in-video/) |
| Lip-sync clips are never slipped after sync | [Tamron / Charley Voorhis](https://tamron-americas.com/blog/tips-to-create-a-performance-music-video-in-as-little-as-two-shots-with-charley-voorhis/) |
| Story in verses and breaks, performance in choruses; story keeps its own order | [Adobe](https://www.adobe.com/creativecloud/video/discover/how-to-make-a-music-video.html), [StudioBinder](https://www.studiobinder.com/blog/how-to-film-and-edit-a-music-video/) |
| No dissolves in fast sections; short ones only in lulls | [Hollyland](https://store.hollyland.com/blogs/creator-hub/beat-edit-in-premiere-pro) |
| Colour: match every angle to a hero wide shot, then the look | [Noam Kroll](https://noamkroll.com/the-best-order-of-operations-for-color-grading-why-it-makes-all-the-difference/) |
| Effects and colour after picture lock: optional, never baked in | [No Film School](https://nofilmschool.com/speed-up-music-video-edit) |

Gaps no existing tool documents well (and this one targets): section-aware pacing, shot-size
grammar, story placement and order, balancing takes, keeping alternates reviewable.

## 2. What Premiere lets us build (evidence in docs/PREMIERE_API_NOTES.md style)

| Feature | Route | Evidence |
|---|---|---|
| Cuts (clip portion at a time) | XML `clipitem` in/out; UXP set in/out + overwrite | VERIFIED |
| Section markers with names | XML sequence markers (retained on import); UXP `Markers.createAddMarkerAction` | VERIFIED |
| Cross dissolve, dip to white | XML `transitionitem` (Premiere maps other transitions to Cross Dissolve); UXP `TransitionFactory` | VERIFIED; UXP cannot read transitions back |
| Zoom punch-in (scale keyframes), opacity | XML basic motion/opacity + keyframes | VERIFIED (Adobe XML import page) |
| Colour (exposure, temperature, tint, saturation, contrast) | UXP Lumetri `AE.ADBE Lumetri` parameters | SECONDARY (third-party, live on 26.5.1) |
| LUT file in Lumetri | none | NOT FOUND: the panel writes .cube files; applying them is a manual step |
| Speed change | XML constant speed only | VERIFIED (high level) |
| Adjustment layer, colour matte | none | NOT FOUND |

## 3. Pipeline

```
master song ──► music map (engine/music.py): tempo, beats, bars, sections, energy
takes ────────► sync (existing) ──► positions, tiers
takes ────────► shot analysis (engine/shots.py): faces/shot size, sharpness, exposure, motion, colour
                       │
music map + sync + shots + style ──► edit planner (engine/edit.py) ──► edit plan (JSON)
                                                                         │
                     ┌───────────────────────────┬───────────────────────┼─────────────────────┐
                     ▼                           ▼                       ▼                     ▼
           FCP7 XML edit sequence      panel places it (UXP)    preview MP4 (FFmpeg)    .cube colour LUTs
           (cuts, markers, motion,     + Lumetri values,        to watch without
            dissolves)                 alternates in Review     Premiere
```

### Music map (built: engine/music.py)
Own numpy/scipy code (librosa would add ~165 MB and LGPL in-process libraries; madmom and Essentia
models are non-commercial; analysis report). Ellis (2007) beat tracking on log-mel flux; 4/4 bar
phase from harmonic change + low-band onsets; McFee & Ellis (2014) Laplacian segmentation at bar
level with local boundary refinement; section names by repetition and loudness.

Measured on 36 synthetic songs (`tools/music_eval.py`): beat F 0.96, downbeats on bar lines 0.88,
tempo within 1% for 32/36 (4 half-tempo at 170 BPM), section boundaries within 3 s: recall 0.81,
precision 0.80; within 2 beats: 0.56; chorus time labelled "chorus" 0.75. Published numbers for the
same method on real songs (SALAMI/Beatles) are lower (boundary F3 0.46-0.58), so section labels are
advisory: pacing is driven by loudness per bar, and section markers can be moved by the editor.

### Shot analysis (built: engine/shots.py)
FFmpeg keyframe pass at 640 px with an explicit BT.709 matrix (2 fps fallback), YuNet face model
(MIT, 233 KB) through OpenCV headless (Apache-2.0); face height / frame height gives wide / medium /
close; Laplacian variance (sharpness); luma percentiles (exposure); frame difference (motion);
Lab mean/std (colour). Takes are grouped into camera setups (median-luma thumbnail correlation) so two takes from the
same setup are not cut together (jump cut). Decisions and thresholds: docs/DECISIONS.md D-026.

### Colour match (built: engine/colour.py)
Hero take = medoid of the takes' colours among well-exposed wide/medium takes. With faces in both
takes, the skin tone is shifted onto the hero's (pure Lab shift); otherwise Reinhard mean/std
transfer of mid-tones; clamped. Looks: warm, cool, teal & orange, black & white, faded film.
Output: 33^3 .cube LUTs (match per take, match + look per take, look alone); applied in the preview
video; in Premiere the editor loads them in Lumetri (no API can).

### Edit planner
1. Cut grid: song start, every section start (forced), beats. Shot length per section from the style
   preset and the bar's loudness; lengths vary (half / base / double) with a cap on runs of 1-beat
   shots; shots of a bar or longer start on a downbeat; optional early-cut offset in frames (0).
2. Story slots: a share of shots (preset) in intros, verses, bridges and low-energy bars, never on a
   chorus downbeat; story clips (unsynced video, or clips the editor marks) play in their own order.
3. Shot choice per slot (deterministic): candidates are synced takes covering the whole slot
   (HIGH/MEDIUM, 1x). Score = quality (face visible, sharp, exposed) + shot-size grammar (alternate,
   wide at intros/section starts) + balance (screen time per take) - penalties (same take or same
   setup as the previous shot: never / strong; used recently). Choruses come back to the same setup.
4. Lip sync is exact: source in = sequence time - clip's synced position (the placement the draft
   already verifies). Takes are never slipped or retimed; the song is never edited.
5. Alternates: the next best takes per shot are stored for the panel's Review.

Style presets: **Performance** (beat/bar pacing), **Performance + story**, **Hype** (fast, punch-ins
and flashes on chorus downbeats), **Ballad** (bar/2-bar pacing, short dissolves in lulls).

### Outputs
- XML sequence "AutoSync Cut - <song>": V1 the cut, A1 the song, section markers, optional motion
  keyframes and dissolves. The synced stack stays as its own sequence for alternates.
- Preview MP4 rendered with FFmpeg (scaled, colour LUTs applied) so the cut can be watched and shared
  before Premiere.
- Per-take colour-match LUTs (.cube 33^3, Adobe Cube LUT spec 1.0) and Lumetri values via the panel.

## 4. Ad mode (research in progress)
Inputs: script or storyboard (shot list CSV/XLSX template first; .docx/.pdf later) and the shoot
footage. Takes are matched to script lines by slate/file names and by local speech-to-text of the
dialogue, best takes chosen, assembled in script order to a target length (15/30/60 s) over the
music bed cut on the beat grid. Details follow the research report.

## 5. Gates
- E1 music map: synthetic accuracy as above (done).
- E3 planner: every cut on a beat or section start; 100% song coverage; no take twice in a row;
  lip-sync source times exact to the frame; deterministic; per-section pacing within preset ranges.
- E4 outputs: XML imports in the fake Premiere and in the client's Premiere (self-test readback);
  preview MP4 frame-checked against the plan.
- E2/E5: face/shot-size checks on public-domain portraits; colour ΔE reduction on synthetic casts.
