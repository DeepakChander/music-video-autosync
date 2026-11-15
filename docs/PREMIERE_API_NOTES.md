# Premiere Pro UXP: placement API reference (target 25.6+ / 26.x, Windows)

Compiled 2026-11-13. Nothing here was executed in Premiere. Every item is backed by an official doc or by code I read.

## Sources and evidence labels

| Label | Meaning |
|---|---|
| **VERIFIED (doc)** | Quoted from developer.adobe.com/premiere-pro/uxp. I read the docs source repo `AdobeDocs/uxp-premiere-pro` @ `ffa53e0` (2026-11-04) and spot-checked the live pages. |
| **VERIFIED (Adobe sample)** | Read in `AdobeDocs/uxp-premiere-pro-samples` @ `e1c8b68` (2026-11-11), `sample-panels/premiere-api/src/*.ts`. This is strong evidence of how an API is meant to be called. |
| **TYPES** | Read in `adobe/premierepro-types` (`@adobe/premierepro` 27.1.0-beta.23, `src/premierepro.d.ts`), which the docs link as "TypeScript Declarations". |
| **SECONDARY** | Third-party evidence. These are live-test claims made by the authors; I did not reproduce them. The main ones are `thinkvp/Syncitol` @ `1638dcb` (`uxp/js/premiere.js`, verified by its author on PPro 26.3 Win) and `tro2789/premiere-uxp-dev-notes` @ `7d0405e` (verified by its author on 26.2–27.0 Win). |
| **NOT FOUND** | Absent from the official docs. The note says where I searched. |

Base URL for the reference: `https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/<class>/`.

### Version gates that matter for a 25.6+ target (VERIFIED (doc), "Since" tags and changelog)
| API / behaviour | Since |
|---|---|
| Core: Project, Sequence, SequenceEditor, TickTime, FrameRate, ClipProjectItem, FolderItem, tracks | 25.6 |
| `SequenceSettings.getVideoFrameRate()` / `setVideoFrameRate()` (sync), `Project.closeSequence`, `ProjectConverter` (FCP XML / OTIO **export**) | 26.2 |
| `Project.createSequenceWithPresetPath`, `ClipProjectItem.createSubClipAction`, `Track.createSetNameAction`. **Action creation must be inside `lockedAccess` (enforced)**. | 26.3 |
| Bug fix: `ClipProjectItem.createSetInPointAction` / `createSetOutPointAction` "no longer error when called" (so they **error on < 26.5**) | 26.5 |
| `Media.getStart()` / `getDuration()` (sync), replacing the deprecated async `media.start` / `media.duration` | 26.5 |
| `Constants.ProjectItemType`, `TickTime.timeToTimecode` / `timecodeToTime`, removal of `ppro.Application` | 27.0 |

Runtime version check (VERIFIED (doc), changelog 27.0, https://developer.adobe.com/premiere-pro/uxp/changelog/): `require("uxp").host.version  // "27.0.0"`.

---

## 1. Module, entry point, locking and transactions — VERIFIED (doc)

URL: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/ and https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project/

| Member | Exact doc signature |
|---|---|
| module | `const app = require('premierepro');` |
| `Project.getActiveProject()` | static, **Returns: Promise\<Project\>** (async) |
| `project.lockedAccess(callback)` | `callback: () => void`, **Returns: void** (sync). "Get a read/upgrade locked access to Project, project state will not change during the execution of callback function. Can call executeTransaction while having locked access." |
| `project.executeTransaction(callback, undoString)` | `callback: (compoundAction: CompoundAction) => void`, `undoString: string` (optional in TYPES), **Returns: boolean** (sync). "Execute undoable transaction by passing compound action" |
| `compoundAction.addAction(action)` | **Returns: boolean**. `compoundAction.empty` is a read-only boolean. |
| `Action` | "This class does not have any methods or properties available." |

**Must actions be created inside lockedAccess? Yes (26.3+).** The changelog for v26.3.0 (https://developer.adobe.com/premiere-pro/uxp/changelog/), under "Action creation in locks", says: *"Creating `Action` instances through the various `create*Action` functions must now be done so while behind a `project.lockedAccess(() => { ... })` call. Previously this wasn't consistently enforced across `create*Action` calls."* It gives the reason: *"many `Action`s contain data that can quickly become stale or inconsistent..."*

ESLint page (https://developer.adobe.com/premiere-pro/uxp/resources/fundamentals/eslint-support/). The `@adobe/eslint-plugin-premierepro` rules cover:
- "Requiring `create*Action()` usage inside `Project.lockedAccess()` or `Project.executeTransaction()`"
- "Disallowing `async` work inside `lockedAccess()` / `executeTransaction()` callbacks"
- "Disallowing `Action` instances from escaping their lock scope"

Sync vs async (index page, VERIFIED (doc)): "In UXP, a method call is *asynchronous*". Also: "all properties (get and set) in the API were designed to be *synchronous*". The Promise / non-Promise return type is listed per method. Action factories return `Action` synchronously.

```js
const ppro = require("premierepro");
const project = await ppro.Project.getActiveProject();
// Do every await BEFORE the lock. No await inside either callback.
let ok = false;
project.lockedAccess(() => {
  const action = someClip.createSetNameAction("New name");            // created inside the lock
  ok = project.executeTransaction((ca) => { ca.addAction(action); }, "Rename clip");
});
if (!ok) throw new Error("transaction refused");
```
SECONDARY (Syncitol): "`project` and `sequence` must come from the SAME getActiveProject() call — mixing wrappers ... makes host objects 'no longer valid'".
SECONDARY (dev-notes): `executeTransaction` returns `true` even for an empty compound action. Check `compoundAction.empty` or read back the result instead.

---

## 2. Creating a new sequence — VERIFIED (doc) + VERIFIED (Adobe sample)

URL: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project/

| Method | Signature | Returns | Since |
|---|---|---|---|
| `createSequence(name, presetPath)` | `name: string, presetPath: string`. TYPES: `presetPath?` is optional. Doc: "Create a new sequence with the default preset path - Parameter presetPath is deprecated, instead use createSequenceWithPresetPath()" | `Promise<Sequence>` | 25.6 |
| `createSequenceWithPresetPath(name, presetPath)` | both `string` | `Promise<Sequence>` | **26.3** |
| `createSequenceFromMedia(name, clipProjectItems, targetBin)` | `string, ClipProjectItem[], ProjectItem`. TYPES: the last two are optional. | `Promise<Sequence>` | 25.6 |

- **Is a preset path required on Windows? No.** `createSequence(name)` uses the default preset. The Adobe sample calls `project.createSequence(sequenceName)` with no preset.
- **Setting frame rate and resolution** goes through `SequenceSettings` plus `sequence.createSetSettingsAction(settings)`. URLs: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequencesettings/ and .../classes/sequence/
  - `sequence.getSettings(): Promise<SequenceSettings>`
  - `settings.setVideoFrameRate(inVideoFrameRate: FrameRate): boolean` (**26.2**, sync). `settings.getVideoFrameRate(): FrameRate` (26.2, sync).
  - `settings.setVideoFrameRect(inVideoFrameRect: RectF): Promise<boolean>` (25.6). `RectF` has `width` and `height` (both W) and a constructor (`new ppro.RectF()`).
  - `sequence.createSetSettingsAction(sequenceSettings): Action`
- The Adobe sample (`src/sequence.ts`, `setVideoFrameRate`) shows the pattern: mutate the settings object, then commit it through the action inside lock plus transaction.
- **On 25.6–26.2** there is no `setVideoFrameRate`. Use the deprecated `createSequence(name, presetPath)` with a `.sqpreset`, or `createSequenceFromMedia`.
  - The sample ships `assets/Social Media Portrait 9x16 30 fps.sqpreset`. It is XML with `<VideoFrameRate>8467200000</VideoFrameRate>` (ticks per frame = 30 fps), `<VideoFrameSize>0,0,1080,1920</VideoFrameSize>`, `<InitialNumberOfVideoTracks>3` and an `<AudioTracks>` JSON array. This is an Adobe sample file, not a documented format.
- NOT FOUND: whether `createSequence` opens or activates the new sequence. Call `openSequence` and `setActiveSequence` yourself (item 9).
- NOT FOUND: whether `createSequenceFromMedia` places the clips or copies their settings.

```js
const seq = await project.createSequence("Auto Placement");
const settings = await seq.getSettings();
settings.setVideoFrameRate(ppro.FrameRate.createWithValue(25));      // 26.2+
const r = new ppro.RectF(); r.width = 1920; r.height = 1080;
await settings.setVideoFrameRect(r);
project.lockedAccess(() => {
  project.executeTransaction((ca) => { ca.addAction(seq.createSetSettingsAction(settings)); }, "Sequence settings");
});
```

---

## 3. Bins, project items and casting — VERIFIED (doc) + VERIFIED (Adobe sample)

URLs: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/folderitem/ · .../projectitem/ · .../clipprojectitem/

| Member | Signature | Returns |
|---|---|---|
| `project.getRootItem()` | — | `Promise<FolderItem>` |
| `project.getInsertionBin()` | — | `Promise<ProjectItem>` |
| `folder.createBinAction(name, makeUnique)` | `string, boolean` | `Action` ("lets users create a new bin") |
| `folder.createMoveItemAction(item, newParent)` | `ProjectItem, FolderItem` | `Action` ("moves the given item to the provided folder item newParent") |
| `folder.createRemoveItemAction(item)` | `ProjectItem` | `Action` |
| `folder.getItems()` | — | `Promise<ProjectItem[]>` |
| `projectItem.getId()` | — | `string` (**sync**) |
| `projectItem.getParentBin()` | — | `FolderItem` (**sync**) |
| `ClipProjectItem.cast(projectItem)` | static | `ClipProjectItem` |
| `FolderItem.cast(projectItem)` | static | `FolderItem` |
| `ProjectItem.cast(item)` | static, `FolderItem` or `ClipProjectItem` | `ProjectItem` |
| `clip.getMediaFilePath()` | — | `Promise<string>` |
| `clip.findItemsMatchingMediaPath(matchString, ignoreSubclips)` | `string, boolean`. An **instance** method: "Returns array of project's items with media paths **containing** match string" | `Promise<ProjectItem[]>` |
| `clip.getContentType()` | — | `Promise<Constants.ContentType>` |

- There is no `createSetParentAction`. NOT FOUND in the reference (all of `classes/*.md` searched). Use `createMoveItemAction`.
- **Cast semantics** (VERIFIED (Adobe sample), `projectPanel.ts#getClipProjectItem`): the cast returns a falsy value when the item is not of that type. The sample tests `if (!clipProjectItemCandidate)` and then tries `FolderItem.cast`.
- `createBinAction` returns no handle to the new bin. The sample re-reads `rootItem.getItems()` and finds the bin by name. Diffing by `getId()` is more robust than a name match.
- The sample calls `createMoveItemAction` on the **current parent** (root): `rootItem.createMoveItemAction(newBin1, ppro.FolderItem.cast(newBin2))`. Whether the receiver must be the current parent: NOT FOUND.
- Path lookup: `findItemsMatchingMediaPath` matches substrings, so post-filter for exact equality. The separator and case of the returned path are not documented. Normalise `\`→`/` and compare case-insensitively on Windows (my recommendation, not verified).
- SECONDARY (dev-notes): `getMediaFilePath()` behaved **sync** in their probes and returns `""` for multicam and nested sources. Always `await` it: awaiting a plain value is harmless.

```js
const root = await project.getRootItem();
const before = new Set((await root.getItems()).map((i) => i.getId()));
project.lockedAccess(() => {
  project.executeTransaction((ca) => { ca.addAction(root.createBinAction("Placement 2026-11-13", true)); }, "Create bin");
});
const binPI = (await root.getItems()).find((i) => !before.has(i.getId()));
const bin = binPI && ppro.FolderItem.cast(binPI);

async function clipsByPath(folder, out = new Map()) {           // recursive walk
  for (const it of await folder.getItems()) {
    const clip = ppro.ClipProjectItem.cast(it);
    if (clip) { const p = await clip.getMediaFilePath(); if (p) out.set(p.replace(/\\/g, "/").toLowerCase(), clip); }
    else { const f = ppro.FolderItem.cast(it); if (f) await clipsByPath(f, out); }
  }
  return out;
}
```

---

## 4. Importing media, and XML — importFiles VERIFIED (doc); XML via importFiles NOT FOUND

URL: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project/

- `project.importFiles(filePaths: string[], suppressUI: boolean, targetBin: ProjectItem, asNumberedStills: boolean): Promise<boolean>`. Doc: "Import files in root/target bin of the project". TYPES: the last three are optional.
- **The return value is only a boolean.** No items come back. Find them by re-reading the target bin (adb-mcp and the sample both diff `getItems()`), or by path (item 3).
- Adobe sample (`import.ts`): `project.importFiles(filePaths, true, null /* null defaults to project root */, false)`.
- **Can importFiles import an FCP7 .xml and create its sequence? NOT FOUND in the official UXP docs.** I searched the `project.md` description, the changelog, the recipes and the whole docs repo for `xml|xmeml|fcp|aaf`. The only hits are the **export** APIs.
  - SECONDARY (dev-notes `cep/fcp-xml-import.md`, PPro 26.5): the **ExtendScript** call `app.project.importFiles([xmlPath], true, app.project.rootItem, false)` imports xmeml v4. It creates a new bin named `<file>.prproj` that holds the media and the sequence. Nothing shows the UXP `importFiles` does the same. **Test it on your build** and detect the result by diffing `project.getSequences()` by `guid`.
  - The GitHub code search (`importFiles xmeml`, `premierepro importFiles ".xml"`) returned no UXP examples.
- Other UXP import APIs (VERIFIED (doc)):
  - `importSequences(projectPath: string, sequenceIds: Guid[]): Promise<boolean>` takes a **.prproj** source.
  - `importAEComps(aepPath, compNames, targetBin)` and `importAllAEComps(aepPath, targetBin)`.
- **No UXP import exists for XML, AAF or OTIO.** `ProjectConverter` (26.2+) only has `exportAsFinalCutProXML(sequence, outputFilePath, suppressUI)`, `exportAsOpenTimelineIO(...)` and `exportAAF(...)` (26.3). TYPES confirm there are no other `import*` methods.

```js
const seqsBefore = new Set((await project.getSequences()).map((s) => s.guid.toString()));
const ok = await project.importFiles(["D:/media/a.mp4", "D:/media/b.mov"], true, bin, false);
// XML fallback (behaviour UNVERIFIED in UXP; test first):
await project.importFiles(["C:/Users/me/AppData/Roaming/X/placement.xml"], true, null, false);
const newSeqs = (await project.getSequences()).filter((s) => !seqsBefore.has(s.guid.toString()));
```

---

## 5. SequenceEditor: insert and overwrite — VERIFIED (doc) + VERIFIED (Adobe sample)

URL: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequenceeditor/ (checked live)

| Member | Signature | Returns |
|---|---|---|
| `SequenceEditor.getEditor(sequenceObject: Sequence)` | static | `SequenceEditor` (**sync**) |
| `editor.createInsertProjectItemAction(projectItem: ProjectItem, time: TickTime, videoTrackIndex: number, audioTrackIndex: number, limitShift: boolean)` | — | `Action` |
| `editor.createOverwriteItemAction(projectItem: ProjectItem, time: TickTime, videoTrackIndex: number, audioTrackIndex: number)` | — | `Action` |
| `editor.createRemoveItemsAction(trackItemSelection, ripple: boolean, mediaType: Constants.MediaType, shiftOverLapping: boolean)` | — | `Action` |
| `editor.createCloneTrackItemAction(trackItem, timeOffset: TickTime, videoTrackVerticalOffset, audioTrackVerticalOffset, alignToVideo, isInsert)` | — | `Action` |

- **Auto-creating tracks:**
  - Insert: VERIFIED (doc). *"Note: If you pass a track index greater than the number of existing tracks, a new track will be created."*
  - Overwrite: the doc has **no such note**. NOT FOUND.
  - How many tracks get created when the index is far beyond the count: NOT FOUND. Use `index == currentCount` and grow one at a time.
- **Other ways to add tracks: NOT FOUND.** There is no `addTrack` / `createAddTrackAction` in the reference or in TYPES. Alternatives:
  - Insert at index = count.
  - Use a sequence preset with more tracks (`InitialNumberOfVideoTracks` / `AudioTracks` in the `.sqpreset`; sample asset, undocumented).
  - Use the XML fallback.
- **Indexes are 0-based.** VERIFIED (Adobe sample), `sequenceEditor.ts`: "Inputing 2 for video track index will always overwrite trackItem at V3." The sample passes `1, 1` for V2/A2.
- `limitShift`: the sample comment says "don't shift non-input track for this insert". The doc gives no description. SECONDARY (dev-notes): it "does **not** reliably prevent ripple in multi-track cases".
- **Video-only or audio-only placement: NOT FOUND.** Neither factory has a media-type parameter. I found no documented meaning for `-1` as a track index (CaYatur/PremiereProMCP tries `-1` only as trial and error). Candidate routes:
  1. `clip.createSubClipAction(name, startTime, endTime, hasHardBoundaries, { takeVideo?: boolean, takeAudio?: boolean })` (**26.3**, VERIFIED (doc)). Make a video-only or audio-only subclip, then insert that. The resulting placement is **UNVERIFIED**.
  2. Insert A/V, then remove the unwanted half with `TrackItemSelection` + `createRemoveItemsAction`. **Unclear:** the sample comments say `mediaType` means "align moved track items to the start of the nearest video frame", not a filter. Whether a linked partner gets removed too is NOT FOUND.
  3. XML fallback. SECONDARY (dev-notes, CEP import): "An audio-only `clipitem` that points to a file with picture and sound places only the audio."
  - SECONDARY (dev-notes): nested-sequence items always create an audio item.
- **Insert vs overwrite for 100–200 clips:** insert ripples the later material on the target tracks. On a fresh empty sequence, **overwrite** at absolute times avoids ripple, so the order of operations stops mattering.
- **Stale-action risk:** the changelog says actions can "become stale". Do not create 200 insert actions up front, before earlier ones are applied. SECONDARY (Syncitol): it builds and commits many `createMoveAction`s in **one** transaction successfully on 26.3. Choose per clip (200 undo steps) or batch, then read back.

```js
const editor = ppro.SequenceEditor.getEditor(seq);
for (const job of jobs) {                                  // job: {clip: ClipProjectItem, ticks: string, v, a}
  let ok = false;
  project.lockedAccess(() => {
    const act = editor.createOverwriteItemAction(ppro.ProjectItem.cast(job.clip),
      ppro.TickTime.createWithTicks(job.ticks), job.v, job.a);
    ok = project.executeTransaction((ca) => { ca.addAction(act); }, `Place ${job.clip.name}`);
  });
  if (!ok) throw new Error(`placement refused: ${job.clip.name}`);
}
```

---

## 6. Source in/out before insert — VERIFIED (doc); behaviour in insert is SECONDARY

URL: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/clipprojectitem/

| Member | Signature | Returns | Note |
|---|---|---|---|
| `clip.createSetInOutPointsAction(inPoint: TickTime, outPoint: TickTime)` | — | `Action` | 25.6, the safe choice on all targets |
| `clip.createSetInPointAction(tickTime: TickTime)` | — | `Action` | **Errors before 26.5** (changelog 26.5 bug fix) |
| `clip.createSetOutPointAction(tickTime: TickTime)` | — | `Action` | Same. The doc description is a copy-paste error ("Sets the in point"). |
| `clip.createClearInOutPointsAction()` | — | `Action` | The correct name. `clearInOutPoints` does not exist. |
| `clip.getInPoint(mediaType)` / `getOutPoint(mediaType)` | `Constants.MediaType` | `Promise<TickTime>` | — |

- NOT FOUND: the time base of the in/out values. The docs do not say whether they are relative to media start or absolute source timecode. For track items, the doc says in/out are "relative to the start time of the project item". The sample's `addHandlesToTrackItem` treats them that way and adds `Column.Intrinsic.MediaStart` to get absolute values.
- SECONDARY (dev-notes, 26.2.x): insert and overwrite "**ignore source in/out marks** set on the source projectItem **in the same transaction**". Insert "applies the marks only partially, with offset and duration errors".
- SECONDARY (julesdue/JDSequenceToolkit `insertMultipleClips.js`): sets in/out in **its own transaction**, then inserts in a later one.
- Recommendation:
  - Set marks in a separate transaction before placing.
  - After placing, verify `trackItem.getInPoint()` / `getOutPoint()` / `getEndTime()`.
  - Alternatively, use `createSubClipAction` (26.3+) with `hasHardBoundaries = true`.

```js
project.lockedAccess(() => {
  const a = clip.createSetInOutPointsAction(ppro.TickTime.createWithTicks(inTicks), ppro.TickTime.createWithTicks(outTicks));
  project.executeTransaction((ca) => { ca.addAction(a); }, "Set source in/out");
});
// ...then a SEPARATE lockedAccess/executeTransaction for the overwrite/insert (item 5), then read back.
```

---

## 7. Tracks and track items — VERIFIED (doc) unless marked

URLs: .../classes/sequence/ · .../classes/videotrack/ · .../classes/audiotrack/ · .../classes/videocliptrackitem/ · .../classes/audiocliptrackitem/

| Member | Returns |
|---|---|
| `sequence.getVideoTrackCount()` / `getAudioTrackCount()` | `Promise<number>` |
| `sequence.getVideoTrack(trackIndex: number)` / `getAudioTrack(trackIndex)` | `Promise<VideoTrack>` / `Promise<AudioTrack>`. **0-based** (sample checks `trackIndex + 1 > videoTrackCount`). |
| `track.getTrackItems(trackItemType: Constants.TrackItemType, includeEmptyTrackItems: boolean)` | `VideoClipTrackItem[]` / `AudioClipTrackItem[]` (**sync**). The doc says the values "can be Empty (0), Clip (1), Transition (2), Preview (3) or Feedback (4)". |
| `track.getIndex()` → `Promise<number>`; `track.name`, `track.id` (R) | — |
| `item.getStartTime()` / `getEndTime()` | `Promise<TickTime>`, in sequence time |
| `item.getInPoint()` / `getOutPoint()` | `Promise<TickTime>`, "relative to the start time of the project item" |
| `item.getName()` → `Promise<string>`; `item.getProjectItem()` → `Promise<ProjectItem>`; `item.getMediaType()` → `Promise<Guid>`; `item.getDuration()`, `getTrackIndex()`, `isDisabled()` | — |
| `item.createMoveAction(tickTime: TickTime)` | `Action`. Doc: "moves the inPoint of the track item to a new time, by shifting it by a number of seconds". |
| `item.createSetStartAction(tickTime)` / `createSetEndAction(tickTime)` | `Action`. Doc: "Create set start time action for sequence". |
| `item.createSetInPointAction` / `createSetOutPointAction(tickTime)` | `Action` |
| `item.createSetDisabledAction(disabled: boolean)` | `Action` (clip-level enable/disable, undoable) |
| `track.setMute(mute: boolean)` | `Promise<boolean>`. Doc: "sets the mute state of the track to muted/unmuted". On **both** AudioTrack and VideoTrack. |
| `track.isMuted()` | `Promise<boolean>` |

- **`createMoveAction` is a delta.** The doc wording ("shifting it by") implies this. SECONDARY (Syncitol and dev-notes, both live): it slides the whole clip by the given amount, and "moving an item does NOT drag its linked items".
- `createSetStartAction`: SECONDARY. Syncitol: "throws 'Invalid parameter' whenever the new start lands inside another clip's span". Dev-notes: "no observed effect" in their case. Prefer placing at the right time from the start, or use `createMoveAction(delta)`.
- **Linked items: NOT FOUND.** There is no link or unlink query or action in the reference or in TYPES. The only link-aware API is `createRazorTrackAction(..., razorLinkedTrackItems, ...)`, which is in TYPES and the sample only (27.1). Syncitol infers links by matching spans across media types.
- **Track mute as an action: NOT FOUND.** There is no `createSetMuteAction`. `setMute` is a direct async call, not an Action, so it is presumably not undoable (NOT VERIFIED). Whether it must run outside the lock is not documented. Call it outside `lockedAccess`, because it returns a Promise and the ESLint rules forbid async work inside the lock.
- SECONDARY (Syncitol): `getMediaFilePath()` "yields to the host and invalidates transient TickTime/trackItem objects". Read the positions into plain numbers or strings first, then resolve the paths.

```js
const a1 = await seq.getAudioTrack(0);
await a1.setMute(true);                                       // track mute (not an Action)
const v1 = await seq.getVideoTrack(0);
for (const it of v1.getTrackItems(ppro.Constants.TrackItemType.CLIP, false)) {
  const s = await it.getStartTime(), e = await it.getEndTime();
  console.log(await it.getName(), s.ticks, e.ticks);         // keep ticks as strings
}
```

---

## 8. TickTime and FrameRate — VERIFIED (doc) unless marked

URLs: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/ticktime/ · .../classes/framerate/

| Member | Signature / type |
|---|---|
| `TickTime.createWithSeconds(seconds: number)` | `TickTime` |
| `TickTime.createWithTicks(ticks: string)` | `TickTime`. Doc: "with ticks **as a string**". |
| `TickTime.createWithFrameAndFrameRate(frameCount: number, frameRate: FrameRate)` | `TickTime` |
| `.seconds` | `number` (R) |
| `.ticks` | **`string`** (R) |
| `.ticksNumber` | `number` (R) |
| `add(t)`, `subtract(t)`, `multiply(factor)`, `divide(divisor)` | new `TickTime`. "This TickTime is not modified." `divide` by 0 gives `TIME_INVALID`. |
| `equals(t)` | `boolean` |
| `alignToFrame(frameRate)`, `alignToNearestFrame(frameRate)` | `TickTime` |
| `TickTime.timeToTimecode` / `timecodeToTime` | 27.0 only |
| `FrameRate.createWithValue(value: number)` | `FrameRate` |
| `frameRate.value` | fps (R) |
| `frameRate.ticksPerFrame` | `number`. Doc: "Read/Write property to get/set ticks per frame." |
| `new ppro.TickTime()` / `new ppro.FrameRate()` | constructors exist |

- **TICKS_PER_SECOND: not an API constant. NOT FOUND** in the reference or TYPES.
  - The value **254016000000** is VERIFIED (Adobe sample). `sequence.ts#addHandlesToTrackItem` has `const ticksPerSec = 254016000000;`.
  - The sample `.sqpreset` stores 30 fps as `8467200000` = 254016000000/30.
- Constants `TickTime.TIME_ZERO`, `TIME_ONE_SECOND`, `TIME_ONE_MINUTE`, `TIME_ONE_HOUR`, `TIME_INVALID`, `TIME_MIN` and `TIME_MAX` are in TYPES and used in the sample. They are **not** on the reference page.
- **Reading a sequence's rate:**
  - `(await seq.getSettings()).getVideoFrameRate()` returns a `FrameRate` synchronously. **26.2+**. Sample and changelog both use it.
  - On 25.6–26.1: `seq.getTimebase(): Promise<string>`. The doc only says "Gets the time base of sequence". The Adobe sample treats it as **ticks per frame**: `ticksPerSec / Number(await sequence.getTimebase())` → fps. SECONDARY (dev-notes) advises against relying on it because the meaning is unspecified.
- **Exact ticks per frame** (my arithmetic from 254016000000; all are integers): 23.976 = `10594584000`, 24 = `10584000000`, 25 = `10160640000`, 29.97 = `8475667200`, 30 = `8467200000`, 50 = `5080320000`, 59.94 = `4237833600`, 60 = `4233600000`.
- **Precision:** `ticksNumber` and `seconds` are doubles. 2^53 ticks ≈ **9.85 h**. Beyond that, integer ticks lose precision in a `number`. Do frame math with `BigInt` on `.ticks` and pass strings to `createWithTicks`.

```js
const TPS = 254016000000n;
const fr = (await seq.getSettings()).getVideoFrameRate();             // 26.2+
const tpf = BigInt(Math.round(fr.ticksPerFrame ?? Number(TPS) / fr.value));
const atFrame = (f) => ppro.TickTime.createWithTicks((BigInt(f) * tpf).toString());
const t = ppro.TickTime.createWithFrameAndFrameRate(250, fr);         // documented alternative
```

---

## 9. Sequence navigation — VERIFIED (doc)

URLs: .../classes/project/ · .../classes/sequence/

| Member | Signature | Returns |
|---|---|---|
| `project.getSequences()` | — | `Promise<Sequence[]>` |
| `project.getActiveSequence()` | — | `Promise<Sequence>` |
| `project.setActiveSequence(sequence)` | `Sequence` | `Promise<boolean>` |
| `project.openSequence(sequence)` | `Sequence` | `Promise<boolean>` ("Open a sequence and return true if successful.") |
| `project.closeSequence(sequence)` | `Sequence` | `Promise<boolean>` (26.2) |
| `project.getSequence(guid)` | `Guid` | `Promise<Sequence \| null>` |
| `sequence.setPlayerPosition(positionTime: TickTime)` | — | `Promise<boolean>` |
| `sequence.getPlayerPosition()` | — | `Promise<TickTime>` |
| `sequence.guid` / `sequence.name` | R | — |

SECONDARY (Syncitol): it calls `openSequence(clone)` and then `setActiveSequence(clone)` to bring the timeline tab up.

```js
await project.openSequence(seq);
await project.setActiveSequence(seq);
await seq.setPlayerPosition(ppro.TickTime.TIME_ZERO);
```

---

## 10. Manifest, network, filesystem, modules, HTML/CSS

**Manifest — VERIFIED (doc).** https://developer.adobe.com/premiere-pro/uxp/plugins/concepts/manifest/ (checked live)
- "`manifestVersion` ... Premiere supports version `"5"`." Hybrid (C++ addon) plugins use `6` (`plugins/hybrid-plugins/build.md`). Syncitol uses 6 because it has an addon.
- `host.app`: `"PS"`, `"ID"`, `"premierepro"` or `"XD"`. `"premierepro"`: Adobe Premiere.
- `minVersion`: "in the `x.y.z` format" (required). `maxVersion` is optional.
- `host` must be a **single object** for packaging. An array is "Only during development" (`plugins/distribution/package/`).
  - SECONDARY (Syncitol README): an array in a packaged build causes "Couldn't install plugin". Dev-notes claims an array is required; that contradicts the official docs, so ignore it.
- `localFileSystem`: `"plugin"` (default), `"request"` or `"fullAccess"`.
  - `"plugin"`: plugin folders only.
  - `"request"`: "request access".
  - `"fullAccess"`: "access the filesystem without requesting access".
- `network.domains`: `string[]` or `"all"`. Example `["https://example.com", "https://*.adobe.com/", "wss://*.myplugin.com"]`.
  - Network recipe: "You may use wildcards for domain patterns, such as `https://api.*.example.com`".
  - Also: "All APIs support both HTTP and HTTPS, but **macOS** restricts `http://`". So plain `http://` is allowed on Windows per the doc.
- **Port syntax and localhost: NOT FOUND in the docs.** I searched the manifest page, the network recipe and both doc repos for `localhost|127.0.0.1|port`.
  - SECONDARY, conflicting:
    - Dev-notes (Premiere 26.2.x): `"http://127.0.0.1:8000"` in `domains` is rejected: "Permission denied to the url ... Manifest entry not found".
    - Forum thread "Allowing Network access to a specific PORT?" (Premiere, 2025-10-30): `"https://127.0.0.1:4443"` failed the same way. No Adobe reply.
    - AdobeDocs/uxp-photoshop#321 (open): one Photoshop user says `"http://127.0.0.1:11434"` works on manifest v5 and `localhost` does not.
  - Dev-notes: `"domains": "all"` works for `ws://127.0.0.1:<port>`.
  - **Safe choice for a local helper: `"domains": "all"`** (a documented value). Otherwise test `"http://127.0.0.1:PORT"` on the exact build.
- SECONDARY (dev-notes): the manifest is re-read only on a plugin reload in UDT, not when the panel is reopened.

```json
{
  "manifestVersion": 5,
  "id": "com.example.placer", "name": "Placer", "version": "1.0.0", "main": "index.html",
  "host": { "app": "premierepro", "minVersion": "25.6.0" },
  "entrypoints": [{ "type": "panel", "id": "placerPanel", "label": { "default": "Placer" } }],
  "requiredPermissions": { "localFileSystem": "fullAccess", "network": { "domains": "all" } }
}
```

**`require("fs")` — VERIFIED (doc).** https://developer.adobe.com/premiere-pro/uxp/uxp-api/reference-js/modules/fs/fs (checked live) and https://developer.adobe.com/premiere-pro/uxp/resources/recipes/filesystem-operations/
- "UXP Provides Node.js style file system API". Schemes: `plugin:`, `plugin-data:`, `plugin-temp:` and `file:`. "If there are no schemes defined ... it considers to have 'file:' scheme".
- `readFile(path, options, callback)` returns `Promise<string|ArrayBuffer>`, e.g. `{encoding: "utf-8"}`.
- The recipe table says `file:/` (arbitrary location) **requires `"fullAccess"`**. Example 6 reads `"C:/Users/user/Desktop/export.txt"` with `fullAccess`.
- **%APPDATA% / environment variables: NOT FOUND** (no `process.env` in the docs). `require("os").homedir()` is documented. Compose `homedir + "/AppData/Roaming/X/token"`. That composition is UNVERIFIED for redirected profiles.
- SECONDARY (Syncitol, 26.3): `require("fs").lstat(absPath)` works with `fullAccess`.

```js
const fs = require("fs"), os = require("os");
const token = (await fs.readFile(`${os.homedir()}/AppData/Roaming/X/token`, { encoding: "utf-8" })).trim();
```

**File picker — VERIFIED (doc).** https://developer.adobe.com/premiere-pro/uxp/uxp-api/reference-js/modules/uxp/persistent-file-storage/file-system-provider/ (checked live)
- `getFileForOpening(options)` returns `Promise<File|Array<File>>`. Options: `initialDomain`, `types: Array<string>` (default `['.*']`), `initialLocation` and `allowMultiple: boolean` (default false). "Return empty if no file was selected."
- `entry.nativePath`: "The platform native file-system path of this entry. Read-only".
- The Adobe sample passes types without a dot: `types: ["prproj"]`.
- `getFolder(options)`: "Gets a folder from the file system via a folder picker dialog ... If the
  user dismisses the picker, `null` is returned instead." Returns `Promise<Folder | null>`
  (VERIFIED (doc): AdobeDocs/uxp-premiere-pro, uxp-api/.../persistent-file-storage/file-system-provider.md,
  checked 15 Nov 2026). Used by "Save shot-list template…".

```js
const lfs = require("uxp").storage.localFileSystem;
const files = await lfs.getFileForOpening({ allowMultiple: true, types: ["mp4", "mov", "xml"] });
const paths = (files || []).map((f) => f.nativePath);
```

**Modules — CommonJS VERIFIED (doc); ES modules NOT FOUND.** https://developer.adobe.com/premiere-pro/uxp/resources/recipes/js-modules/
- "UXP uses the CommonJS module system with `require()` statements ... and `module.exports`".
- "The `require()` function in UXP uses relative paths and doesn't search global paths. Always specify the complete relative path ... including the `.js` extension."
- `<script type="module">` and `import`: NOT FOUND anywhere in either doc repo. The script page only shows `<script src="index.js"></script>`.
- SECONDARY (Syncitol): a `require()` inside a file loaded via `<script src="js/main.js">` resolves from the **plugin root**, not `js/`.
- SECONDARY (dev-notes): `require("./folder")` does not resolve `index.js`.

**HTML/CSS limits — VERIFIED (doc).**
- `HTMLCanvasElement`: "supports only basic shapes for now". Only the `'2d'` context. Since UXP v7.0.0. https://developer.adobe.com/premiere-pro/uxp/uxp-api/reference-js/global-members/html-elements/html-canvas-element/
- Known issues (https://developer.adobe.com/premiere-pro/uxp/uxp-api/known-issues/):
  - "createLinearGradient(), createRadialGradient() and clearRect()" do not work on **Windows**.
  - "Not all SVG files are supported ... targeted for simple icons".
  - "CSS transitions and animations are not supported."
- Unsupported elements: `<ul>`/`<li>` are treated as `<div>`; `<ol>` is unsupported; `<title>` may render. https://developer.adobe.com/premiere-pro/uxp/uxp-api/reference-html/general/unsupported-elements/

---

## 11. Installing and packaging — VERIFIED (doc)

URLs: https://developer.adobe.com/premiere-pro/uxp/plugins/ · .../plugins/distribution/install/ (checked live) · .../plugins/distribution/package/ · .../plugins/tutorials/udt-deep-dive/plugin-management/ · .../introduction/essentials/dev-tools/

- **Premiere Developer Mode:** "Open the **Settings** menu, select the **Plugins** tab and select the **Enable developer mode** checkbox. You will need to restart Premiere". The install page adds: "Before you can connect to a UXP plugin running within Premiere Pro, you must enable 'Developer Mode'".
  - UDT also needs developer mode on first launch. The manual route: `%CommonProgramFiles%/Adobe/UXP/Developer/settings.json` containing `{ "developer" : true }` (admin rights).
- **UDT workflow** (UDT v2.2+):
  1. "click the **Add Plugin** button and point the file picker to the `manifest.json`".
  2. Then **Load** (or **Load & Watch**) in the plugin's row, with Premiere running and connected.
  3. Manifest changes need **Unload**, then **Load & Watch**.
- Panels appear under **Window > UXP Plugins**.
- **.ccx:** "A `.ccx` file is a regular ZIP file under the hood ... we suggest **not to create it manually**".
  - UDT packages via the row's **•••** → **Package** (output e.g. `Test-xjluvc_premierepro.ccx`).
  - No digital signature is needed.
  - The doc does not state that manifest.json must sit at the zip root. SECONDARY (Syncitol `build-ccx.js`): it zips the stage contents with "manifest.json at archive root" and forward-slash entry names.
- **Install an unsigned .ccx on Windows:**
  - Double-click it. CC Desktop shows a non-Marketplace warning; click **Install**. Admin rights may be needed, depending on permissions.
  - Or use UPIA:
    ```
    cd "C:\Program Files\Common Files\Adobe\Adobe Desktop Common\RemoteComponents\UPI\UnifiedPluginInstallerAgent"
    UnifiedPluginInstallerAgent.exe /install "C:\Temp\Test-xjluvc_premierepro.ccx"
    UnifiedPluginInstallerAgent.exe /list all
    UnifiedPluginInstallerAgent.exe /remove "<name>"
    ```
- SECONDARY:
  - Syncitol: a double-click install needs **no** Developer Mode.
  - Dev-notes:
    - UPIA is silent; read `%APPDATA%\Adobe\UPI\Log\Upia.log`.
    - Status `-160` happens when the .ccx is on a different drive than Adobe or the path has spaces.
    - Reinstalling the **same version** is a silent no-op. Run `/remove` first, or bump `version`.
    - CC Desktop must be running.
    - Installed copy: `C:\Program Files\Common Files\Adobe\UXP\Plugins\<user>\External\<id>_<version>\`.

---

## 12. FCP7 XML (xmeml v5) for Premiere import

**Spec — VERIFIED (Apple official, retired) documents.**
- Elements: https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/FinalCutPro_XML/Elements/Elements.html
- Timing: .../Topics/Topics.html
- Rates: .../FrameRate/FrameRate.html
- Basics: .../Basics/Basics.html

What the spec says:
- Root: `<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE xmeml><xmeml version="5">`.
- Legend: "The plus (+) sign precedes a subelement that is required."
  - `clipitem` required: `+name, +duration, +rate, +start, +end`.
  - `file` required: `+duration, +rate, +name or +pathurl`.
  - `sequence` required: `+name, +duration, +rate`.
- `rate` = `timebase` (integer) + `ntsc` (Boolean). "If you set `ntsc` to `TRUE`, the NTSC rate reduction (0.01%) is applied". Table B-1:
  - 23.976 = `24/true`; 24 = `24/false`; 25 = `25/false`
  - 29.97 = `30/true`; 30 = `30/false`; 50 = `50/false`
  - 59.94 = `60/true`; 60 = `60/false`
- `start`/`end`: "encode the start and end of a clipitem in an enclosing sequence". `-1` means "preceded/followed by a transition".
- `in`/`out`: "specify the media content", "interpreted based on the frame rate in effect at the time".
- Frame numbering: "for `start` and `in`, Final Cut numbers the first frame as frame 0 ... for `out` and `end` ... as frame 1". So `end` and `out` are exclusive, and `duration = out-in = end-start`.
- Rate inheritance: "Clips that are subelements of this sequence inherit this frame rate unless you specifically encode a different rate".
- `pathurl`: "The URL must start with `file://localhost` or `file:///`". Characters illegal under RFC 2396 must be escaped "in hexadecimal notation preceded by the `%` character". So a space becomes `%20`.
- `enabled` (parents include `track` and `clipitem`): "specifying whether or not the parent element is enabled"; the default is `TRUE`.
- `link`: `linkclipref` OR (`mediatype` [`video` | `audio`], `trackindex`, `clipindex`, `groupindex`). `groupindex` applies to audio only, for a stereo pair.
- `file id` reuse: the first occurrence carries the full definition; later ones use `<file id="..."/>` (Basics, Listing 3-14).

**Premiere-specific behaviour — Adobe docs not retrievable.** helpx.adobe.com `/premiere-pro/using/importing-xml-project-files-final.html` returned HTTP 403 to WebFetch and curl, so this section relies on secondary evidence.

- **Windows pathurl.** SECONDARY (dev-notes, PPro 26.5): `file://localhost/C%3a/...` (colon encoded) "operates correctly". Use `file://localhost/D%3a/My%20Folder/clip%201.mp4`, with forward slashes and each segment percent-encoded. `file://localhost/D:/...` is NOT VERIFIED for Premiere.
- **Units with mixed frame rates.** SECONDARY (dev-notes, PPro 26.5, verified 2026-10-23): "for a 24 fps clip in a 30 fps sequence, write the `clipitem` in and out values in SEQUENCE frames". With the clip's own `<rate>` of 24 and in/out in 24 fps frames, "Premiere read the values as 30 fps frames. All in points were at 0.8 times the correct value."
  - So express `start`/`end`/`in`/`out` in the **sequence** timebase. Expect sub-frame rounding for non-integer ratios.
  - Bake NTSC vs non-NTSC conversions in ticks, then round once to sequence frames.
- **`<enabled>FALSE</enabled>` on an audio `<track>`: NOT FOUND.** Neither Apple nor Adobe defines a track-level meaning; no source maps it to Premiere track mute. Do not rely on it. After import, mute via UXP `audioTrack.setMute(true)` (item 7). For clip-level, `clipitem <enabled>FALSE</enabled>` is the spec's "disabled".
- **Linking.** Spec as above. SECONDARY (dev-notes): "A `clipitem` with `<sourcetrack>` track index 1 imports as a mono clip". A second track referencing the same clip was dropped. Stereo needs care (`groupindex`).
- **Golden reference (recommended).** Build one sample sequence in Premiere by hand, then call `ProjectConverter.exportAsFinalCutProXML(sequence, outputFilePath, suppressUI)` (26.2+, VERIFIED (doc)). Diff your generator against Premiere's own writer (pathurl encoding, `<link>` blocks, muted-track serialisation).
- SECONDARY (dev-notes): media and sequence import into a new bin `<file>.prproj`. A re-import can reuse an existing master clip, so do not delete "orphan" masters blindly.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5"><sequence id="seq-1"><name>Placement</name><duration>750</duration>
 <rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>
 <media><video><track>
  <clipitem id="ci-v1"><name>clip 1</name><duration>750</duration>
   <rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>
   <start>100</start><end>350</end><in>0</in><out>250</out>   <!-- sequence frames; end/out exclusive -->
   <file id="f-1"><name>clip 1.mp4</name><pathurl>file://localhost/D%3a/Media/clip%201.mp4</pathurl>
    <rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate><duration>750</duration></file>
   <link><linkclipref>ci-v1</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex><clipindex>1</clipindex></link>
   <link><linkclipref>ci-a1</linkclipref><mediatype>audio</mediatype><trackindex>1</trackindex><clipindex>1</clipindex><groupindex>1</groupindex></link>
  </clipitem></track></video>
  <audio><track><clipitem id="ci-a1"><name>clip 1</name><duration>750</duration>
   <rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate><start>100</start><end>350</end><in>0</in><out>250</out>
   <file id="f-1"/><sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>
   <link><linkclipref>ci-v1</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex><clipindex>1</clipindex></link>
   <link><linkclipref>ci-a1</linkclipref><mediatype>audio</mediatype><trackindex>1</trackindex><clipindex>1</clipindex><groupindex>1</groupindex></link>
  </clipitem></track></audio></media></sequence></xmeml>
```
(The structure follows the Apple spec. Premiere's acceptance of this exact file is UNVERIFIED. The `link` sub-element syntax mixes `linkclipref` with indexes as many exporters do, but the spec says "linkclipref **or** (mediatype, trackindex, clipindex, groupindex)". Validate it against a Premiere export.)

---

## Status summary

| # | Item | Status |
|---|---|---|
| 1 | module, getActiveProject, lockedAccess, executeTransaction, lock rule | VERIFIED (doc + changelog + ESLint page) |
| 2 | createSequence / WithPresetPath / FromMedia; settings via SequenceSettings | VERIFIED. Auto-open after create: NOT FOUND |
| 3 | root, bins, move, cast, getMediaFilePath, findItemsMatchingMediaPath | VERIFIED. `createSetParentAction`: NOT FOUND (does not exist) |
| 4 | importFiles signature / boolean return | VERIFIED. **XML through UXP importFiles: NOT FOUND** (ExtendScript works per SECONDARY). No UXP XML/AAF/OTIO import. |
| 5 | getEditor, insert/overwrite signatures, 0-based, insert auto-creates track | VERIFIED. Overwrite auto-create, addTrack, video-only/audio-only: NOT FOUND |
| 6 | createSetInOutPointsAction / createSetIn/OutPointAction / createClearInOutPointsAction | VERIFIED (In/Out single error < 26.5). In/out time base and insert honouring marks: NOT FOUND / SECONDARY |
| 7 | track counts and getters, getTrackItems, item getters, move / setStart, setMute / isMuted | VERIFIED. Linked-item API and mute Action: NOT FOUND. Move-is-delta: doc wording + SECONDARY |
| 8 | TickTime / FrameRate API, `.ticks` string | VERIFIED. TICKS_PER_SECOND constant: NOT FOUND (value 254016000000 VERIFIED from Adobe sample) |
| 9 | setActiveSequence, openSequence, setPlayerPosition, getSequences | VERIFIED |
| 10 | manifest v5, "premierepro", localFileSystem values, fs, picker, nativePath, CommonJS, canvas/svg | VERIFIED. **localhost/port domain syntax: NOT FOUND** (SECONDARY says rejected; use `"all"`). ES modules and env vars: NOT FOUND |
| 11 | Dev Mode, UDT Add/Load, Package, .ccx = zip, double-click, UPIA path and commands | VERIFIED. Zip-root layout: SECONDARY |
| 12 | xmeml element rules, rate table, pathurl rules, link, enabled | VERIFIED (Apple). Premiere's mapping of track `<enabled>`, Windows pathurl form and mixed-rate units: SECONDARY / NOT FOUND |

---

## 13. Follow-up

Same sources and commits as above, plus `adobe/eslint-plugin-premierepro` README @ `d45d0c2` (2026-11-12) and `Adobe-CEP/Samples` (Adobe's CEP sample, ExtendScript). The URLs marked "live" were fetched today and match the repo source.

### 13.1 Project panel selection — VERIFIED (doc) + VERIFIED (Adobe sample)
URLs: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/projectutils/ (live) · .../classes/projectitemselection/

| Member | Returns | Since | Doc text |
|---|---|---|---|
| `ProjectUtils.getSelection(project: Project)` (static) | `Promise<ProjectItemSelection>` | 25.6 | "Get array of selected project items in project view" |
| `projectItemSelection.getItems()` | `Promise<ProjectItem[]>` | 25.6 | "Get the project items that is represented by this selection." |
| `ProjectUtils.getProjectViewIds()` | `Promise<Guid[]>` | 25.6 | "Get array of project view ids" |
| `ProjectUtils.getSelectionFromViewId(guid: Guid)` | `Promise<ProjectItemSelection>` | 25.6 | for a specific Project panel |

- Both calls are async.
- The items come back as `ProjectItem`. Cast them with `ClipProjectItem.cast` or `FolderItem.cast`; a cast returns falsy when the type does not match (item 3).
- The user may select bins, so recurse into them as the sample's `getClipProjectItem` does.
- Change event: `ppro.Constants.ProjectEvent.PROJECT_ITEM_SELECTION_CHANGED` (constants page, 25.6). The sample `eventManager.ts` registers it with `ppro.EventManager.addGlobalEventListener(ppro.Constants.ProjectEvent.PROJECT_ITEM_SELECTION_CHANGED, handler, true)`. The event payload is not documented, so re-query with `getSelection` inside the handler.

```js
const sel = await ppro.ProjectUtils.getSelection(project);        // sample: projectPanel.ts#getSelectedProjectItems
const picked = await sel.getItems();
const clips = picked.map((p) => ppro.ClipProjectItem.cast(p)).filter(Boolean);
```

### 13.2 TrackItemSelection and createRemoveItemsAction
URLs: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/trackitemselection/ · .../classes/sequence/ · .../classes/sequenceeditor/

**Building a selection — VERIFIED (doc).** There are two documented routes.

**(a) From the sequence** (the pattern the Adobe sample uses, in `sequence.ts#setSequenceSelection` and `sequenceEditor.ts`):
- `sequence.getSelection(): Promise<TrackItemSelection>`: "Returns the current selection group of the sequence."
- `sel.addItem(trackItem: VideoClipTrackItem | AudioClipTrackItem, skipDuplicateCheck: boolean): boolean` (sync). TYPES: `skipDuplicateCheck?` is optional.
- `sel.removeItem(trackItem): boolean` (sync). `sel.getTrackItems(): Promise<Array<VideoClipTrackItem | AudioClipTrackItem>>`.
- `sequence.setSelection(sel)` pushes it to the UI. It returns `boolean` **sync from 26.3**, `Promise<boolean>` before that. `sequence.clearSelection(): Promise<boolean>`.
- NOT FOUND: whether mutating the object from `getSelection()` changes the on-screen selection without `setSelection`.
- SECONDARY (CaYatur/PremiereProMCP `plugin/src/handlers/clip.js` @ `21a489f`): its `buildSingleItemSelection` calls `getSelection()`, `removeItem()` on every existing item, then `addItem(target)`. It passes that object to `createRemoveItemsAction` without calling `setSelection`.

**(b) Empty selection:** `TrackItemSelection.createEmptySelection(callback: (selection: TrackItemSelection) => void): boolean` (25.6).
- The doc says: *"The selection object is valid for the lifetime and scope of the callback; it's not recommended to extract or use the selection object from outside the callback, nor use asynchronous code within the callback"*.
- It **returns a boolean, not the selection**.
- ESLint rules `no-async-in-empty-selection` and `no-empty-selection-escape` enforce this. The rule doc warns: "selection here is the boolean return value of createEmptySelection(), not the selection!"
- No Adobe sample calls it. The nested pattern below follows these rules, but I could not confirm it works at runtime.

**`editor.createRemoveItemsAction(trackItemSelection: TrackItemSelection, ripple: boolean, mediaType: Constants.MediaType, shiftOverLapping: boolean): Action`** (25.6). TYPES: `shiftOverLapping?` is optional. The doc description is only "Create remove action for sequence", and every parameter description is "-".
- **Does it remove only the selected items? NOT FOUND in the docs.**
  - The Adobe sample `removeSelectedTrackItems` passes the user's current selection ("Remove selected trackItems ... by ripple delete").
  - SECONDARY (CaYatur code): it explicitly adds the linked A/V partners to the selection ("The linked audio/video half has to go too"). That suggests linked partners are **not** removed automatically. It is a design comment, not a stated live test.
- `ripple`: the sample calls it "ripple delete". SECONDARY (dev-notes): `false` = Lift (the gap stays), `true` = Extract.
- **`mediaType`: unresolved.**
  - The only official hint is the Adobe sample comment: `ppro.Constants.MediaType.VIDEO // align moved track items to the start of the nearest video frame`. That reads as **alignment of rippled material, not a filter**.
  - SECONDARY (dev-notes) reads it differently: "`MediaType.ANY` removes video, audio, and caption items in one call". CaYatur passes `ANY` whenever the selection mixes audio and video.
  - Test with `ripple=false` first, where alignment cannot matter.
- `shiftOverLapping`: NOT FOUND. SECONDARY (dev-notes): "not verified ... Start with `false`".

```js
// All awaits happen before the lock: collect the exact items to remove first.
const a1 = await seq.getAudioTrack(0);
const victims = a1.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);   // sync; filter beforehand
const editor = ppro.SequenceEditor.getEditor(seq);
let ok = false;
project.lockedAccess(() => {
  ppro.TrackItemSelection.createEmptySelection((sel) => {                    // UNVERIFIED nesting
    victims.forEach((it) => sel.addItem(it, true));
    const act = editor.createRemoveItemsAction(sel, false /* lift */, ppro.Constants.MediaType.AUDIO, false);
    ok = project.executeTransaction((ca) => { ca.addAction(act); }, "Lift audio halves");
  });
});
// Route (a) instead: const sel = await seq.getSelection(); remove/add items; then create the action inside lockedAccess.
```

### 13.3 Constants and telling video from audio
URL: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/constants/ and TYPES `export namespace Constants`.

**`Constants.TrackItemType`** (25.6):
- Constants page names: `EMPTY, CLIP, TRANSITION, PREVIEW, FEEDBACK`.
- TYPES: `export enum TrackItemType { EMPTY, CLIP, TRANSITION, PREVIEW, FEEDBACK }`, with no explicit values.
- The VideoTrack page states the numbers: "Empty (0), Clip (1), Transition (2), Preview (3) or Feedback (4)".
- The sample uses `ppro.Constants.TrackItemType.CLIP` (`sequence.ts`, `transition.ts`).
- SECONDARY (Syncitol, 26.3): at runtime `CLIP` is `1`.

**`Constants.MediaType`** (25.6):
- Constants page names: `ANY, DATA, VIDEO, AUDIO`.
- TYPES: `export enum MediaType { ANY, DATA, VIDEO, AUDIO }`, with no explicit values.
- **Runtime values: NOT FOUND.** Use the symbols and never hard-code numbers.
- The sample uses `ppro.Constants.MediaType.VIDEO` and `.AUDIO`, including as object keys in `projectPanel.ts` (`MEDIA_TYPE_TO_STRING_MAP`). That shows they are primitives but not what they are.

**Video vs audio item:**
- **Recommended: classify by the track you read it from.** This is VERIFIED (doc): `VideoTrack.getTrackItems` "Returns array of VideoClipTrackItem" and `AudioTrack.getTrackItems` "Returns array of AudioClipTrackItem". No Guid comparison is needed.
- `trackItem.getMediaType(): Promise<Guid>` is "UUID representing the underlying media type of this track item". Tracks have the same method.
- `Guid` has `toString(): string`, plus static `ppro.Guid.fromString(stringValue)` (TYPES).
- **What to compare the Guid against: NOT FOUND** in the UXP docs, samples or TYPES. `Constants.MediaType` is not documented as a Guid.
- SECONDARY (Adobe's CEP sample, ExtendScript, `Adobe-CEP/Samples/PProPanel/jsx/PPRO/Premiere.jsx`):
  - `MediaType_VIDEO = "228CDA18-3625-4d2d-951E-348879E4ED93"`
  - `MediaType_AUDIO = "80B8E3D5-6DCA-4195-AEFB-CB5F407AB009"`
  - `MediaType_ANY = "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF"`
  - The sample's comment calls them "Magical constants from Premiere Pro's internal automation". Whether UXP `getMediaType().toString()` returns these strings is UNVERIFIED. Probe once and compare case-insensitively.

### 13.4 ProjectItem naming and identity — VERIFIED (doc) unless marked
URLs: .../classes/projectitem/ · .../classes/clipprojectitem/ · .../classes/folderitem/ · .../classes/videocliptrackitem/

**Names:**
- `name: string` is a **read-only property** (25.6) on `ProjectItem`, `ClipProjectItem` and `FolderItem`: "The name of this project item."
- Project items have **no `getName()`** (absent from all three pages and from TYPES). Rename with `createSetNameAction(inName: string): Action`.
- `getName(): Promise<string>` exists on **track items** only: "Returns the display name for trackItem". It can differ from the project item's name.

**Identity:**
- `ProjectItem.getId(): string` is **sync**, 25.6: "Get id of projectItem". It is documented **only on ProjectItem**; the ClipProjectItem and FolderItem pages and TYPES do not list it.
- So keep the original `ProjectItem`, or call `ppro.ProjectItem.cast(clip).getId()`. `ProjectItem.cast` takes "FolderItem or ClipProjectItem".
- `trackItem.getProjectItem(): Promise<ProjectItem>`.
- `===` between wrappers: NOT documented. SECONDARY (dev-notes): "Compare with `getId()`. Object identity (`===`) is correct only in some cases." CaYatur also compares `getId()`.
- Sequences: compare `sequence.guid.toString()`.

```js
const placedId = ppro.ProjectItem.cast(clip).getId();             // record at placement time
const ti = (await seq.getVideoTrack(0)).getTrackItems(ppro.Constants.TrackItemType.CLIP, false)[0];
const isMine = (await ti.getProjectItem()).getId() === placedId;
```

### 13.5 Timers, os/fs, opening a folder
- **`setTimeout` / Promise sleep: available.** There is no dedicated reference page; I searched `uxp-api/reference-js/global-members` in both doc repos. VERIFIED by official usage:
  - Premiere's JS-modules recipe has `return new Promise(resolve => setTimeout(resolve, 1000));` (https://developer.adobe.com/premiere-pro/uxp/resources/recipes/js-modules/).
  - The network recipe has `setTimeout(() => controller.abort(), timeoutMs)`.
  - The Adobe sample `projectPanel.ts` calls `setTimeout(async () => {...}, 1000)`.
  - Yield **between** transactions only. The ESLint rules forbid async work inside `lockedAccess` / `executeTransaction`.
- **`require("os").homedir()`:** VERIFIED (live) at https://developer.adobe.com/premiere-pro/uxp/uxp-api/reference-js/modules/os/os. "Gets the home directory path of the user", `Returns: string`.
- **`require("fs").readFile(path, options, callback)` → `Promise<string|ArrayBuffer>`:** VERIFIED (live) at https://developer.adobe.com/premiere-pro/uxp/uxp-api/reference-js/modules/fs/fs. Absolute paths need `"fullAccess"` (filesystem recipe, item 10).
- **Opening a folder:** `require("uxp").shell.openPath(path: string, developerText: string): Promise<string>`. VERIFIED (live) at https://developer.adobe.com/premiere-pro/uxp/uxp-api/reference-js/modules/uxp/shell/shell.
  - "Opens the given file or folder path in the system default application." It resolves with `""` on success, otherwise an error message.
  - It needs `requiredPermissions.launchProcess`. The manifest page lists `LaunchProcessPermission` with `schemes: string[]` and `extensions: string[]`, both in the "Required Properties" column, with the example `"extensions": ["pdf"]`.
  - The external-process recipe (https://developer.adobe.com/premiere-pro/uxp/resources/recipes/external-process/) says: "Use an **empty string** `""` to allow **opening folders** in Finder/Explorer". Its example is `"extensions": [".pdf", ".txt", ".mp4", ""]`. That example uses dotted extensions and omits `schemes`, unlike the manifest page; which form is accepted is UNVERIFIED.
  - The recipe also says "the user must provide explicit consent through a system dialog". The choice "can be remembered".
  - `openExternal` does not allow the file scheme: "file scheme is not allowed for openExternal. Use openPath".

```js
await new Promise((r) => setTimeout(r, 0));                       // yield between batches (outside locks)
const res = await require("uxp").shell.openPath("C:\\Users\\me\\Exports", "Open the export folder");
if (res !== "") console.error(res);
// manifest: "launchProcess": { "schemes": ["https"], "extensions": [""] }   // shape UNVERIFIED, see above
```

### 13.6 A sequence's ProjectItem, and moving it into a bin — VERIFIED (doc)
URL: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequence/

- `sequence.getProjectItem(): Promise<ProjectItem>`, Since **25.6**: "Get the associated projectItem of the sequence."
- Reverse direction (ClipProjectItem page): `clip.isSequence(): Promise<boolean>`, `clip.getSequence(): Promise<Sequence>`.
- Moving it: `folder.createMoveItemAction(item: ProjectItem, newParent: FolderItem): Action` (item 3).
  - The Adobe sample calls it on the item's current parent (root). Use `seqPI.getParentBin()` (sync `FolderItem`) as the receiver.
  - Whether any other folder works as the receiver: NOT FOUND.
- NOT FOUND: which bin `createSequence` puts the new sequence in (root vs insertion bin). Read `getParentBin()`.
- NOT FOUND: whether `createSequenceFromMedia(name, clips, targetBin)` puts the sequence itself in `targetBin`. The doc only says "Create a new sequence with a given name and media".
- SECONDARY (Syncitol, 26.3): it renames a sequence with `(await sequence.getProjectItem()).createSetNameAction(newName)` inside a transaction.

```js
const seq = await project.createSequence("Auto Placement");
const seqPI = await seq.getProjectItem();
const from = seqPI.getParentBin();                                  // sync FolderItem
project.lockedAccess(() => {
  const mv = from.createMoveItemAction(seqPI, targetBin);           // targetBin: FolderItem (item 3)
  project.executeTransaction((ca) => { ca.addAction(mv); }, "Move sequence into bin");
});
const parentNow = (await seq.getProjectItem()).getParentBin();        // re-fetch, then compare ids (UNVERIFIED that the wrapper refreshes)
const movedOk = ppro.ProjectItem.cast(parentNow).getId() === ppro.ProjectItem.cast(targetBin).getId();
```

| 13.x | Status |
|---|---|
| 13.1 selection | VERIFIED (doc + sample) |
| 13.2 build selection | VERIFIED. `createEmptySelection` nesting is UNVERIFIED at runtime. Remove semantics (linked partners, `mediaType`, `shiftOverLapping`): NOT FOUND / conflicting SECONDARY. |
| 13.3 constants | Names VERIFIED. TrackItemType numbers VERIFIED (doc). MediaType runtime values and the Guid comparison target: NOT FOUND (CEP Guids are SECONDARY). |
| 13.4 naming / identity | VERIFIED. `===` semantics: NOT FOUND |
| 13.5 timers, os, fs, openPath | VERIFIED. `launchProcess` exact shape: inconsistent between doc pages. |
| 13.6 sequence project item | VERIFIED. Default bin and `createSequenceFromMedia` targetBin effect: NOT FOUND |
