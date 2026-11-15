"use strict";
/*
 * Premiere Pro operations shared by both adapters. Every API below is listed in
 * docs/PREMIERE_API_NOTES.md with its source; reference base URL:
 *   https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/<class>/
 * Rules applied everywhere (notes item 1, changelog 26.3 "Action creation in locks"):
 *   - create*Action() only inside project.lockedAccess(); no await inside the lock;
 *   - actions never escape their lock; executeTransaction's boolean is checked.
 * ppro, core and log are injected so this file runs against a fake Premiere in Node tests.
 */

class UserError extends Error {}

function createPremiere(ppro, core, log) {
  const L = log || { info() {}, warn() {} };

  // https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project/ (getActiveProject: Promise<Project>)
  async function activeProject() {
    const p = await ppro.Project.getActiveProject();
    if (!p) throw new UserError("Open a Premiere Pro project first.");
    return p;
  }

  // project.lockedAccess(cb) + project.executeTransaction(cb(compoundAction), undoName) -> boolean
  function txn(project, undoName, makeActions) {
    let ok = false;
    let err = null;
    let count = 0;
    project.lockedAccess(() => {
      try {
        const actions = makeActions().filter(Boolean);
        count = actions.length;
        if (!count) { ok = true; return; }
        ok = project.executeTransaction((ca) => { for (const a of actions) ca.addAction(a); }, undoName);
      } catch (e) {
        err = e;
      }
    });
    if (err) throw err;
    if (!ok) throw new Error(`Premiere refused the step "${undoName}"`);
    return count;
  }

  function idOf(projectItem) {
    // getId() is documented on ProjectItem (sync). Cast first (notes 13.4).
    const pi = ppro.ProjectItem.cast(projectItem) || projectItem;
    return pi.getId();
  }

  // FolderItem: getItems(): Promise<ProjectItem[]>; createBinAction(name, makeUnique): Action
  async function findBin(parent, name) {
    for (const it of await parent.getItems()) {
      if (it.name === name) {
        const f = ppro.FolderItem.cast(it);
        if (f) return f;
      }
    }
    return null;
  }

  async function ensureBin(project, parent, name) {
    const existing = await findBin(parent, name);
    if (existing) return existing;
    const before = new Set((await parent.getItems()).map((i) => i.getId()));
    txn(project, `AutoSync: create bin ${name}`, () => [parent.createBinAction(name, false)]);
    for (const it of await parent.getItems()) {
      if (!before.has(it.getId()) && it.name === name) {
        const f = ppro.FolderItem.cast(it);
        if (f) return f;
      }
    }
    throw new Error(`Bin "${name}" was not created`);
  }

  // Walk every bin; ClipProjectItem.getMediaFilePath(): Promise<string>
  async function walkClips(folder, out) {
    const map = out || new Map();
    for (const it of await folder.getItems()) {
      const clip = ppro.ClipProjectItem.cast(it);
      if (clip) {
        let p = "";
        try { p = await clip.getMediaFilePath(); } catch (e) { p = ""; }
        if (p && !map.has(core.normPath(p))) map.set(core.normPath(p), clip);
        continue;
      }
      const f = ppro.FolderItem.cast(it);
      if (f) await walkClips(f, map);
    }
    return map;
  }

  // project.importFiles(paths, suppressUI, targetBin, asNumberedStills): Promise<boolean>.
  // It returns only a boolean, so items are found again by media path (notes item 4).
  async function importAndMap(project, paths, targetBin) {
    const root = await project.getRootItem();
    let map = await walkClips(root);
    const missing = [...new Set(paths.filter((p) => p && !map.has(core.normPath(p))))];
    if (missing.length) {
      // targetBin is typed ProjectItem in the reference; a FolderItem is cast to it.
      const bin = targetBin ? (ppro.ProjectItem.cast(targetBin) || targetBin) : null;
      const ok = await project.importFiles(missing, true, bin, false);
      if (!ok) L.warn(`importFiles returned false for ${missing.length} file(s)`);
      map = await walkClips(root);
    }
    const notFound = paths.filter((p) => p && !map.has(core.normPath(p)));
    return { map, notFound, imported: missing.length - notFound.length };
  }

  /*
   * New sequence with the plan's frame rate and size. createSequence(name) uses the default
   * preset; frame rate is set through SequenceSettings.setVideoFrameRate (26.2+) and committed
   * with sequence.createSetSettingsAction (notes item 2), then read back and verified.
   */
  async function createSequence(project, plan) {
    const seq = await project.createSequence(plan.sequenceName);
    if (!seq) throw new Error("Premiere did not create the sequence");
    const settings = await seq.getSettings();
    if (typeof settings.setVideoFrameRate !== "function") {
      throw new UserError("Setting a sequence frame rate needs Premiere Pro 26.2 or later. Update Premiere or use the XML placement method.");
    }
    const fr = ppro.FrameRate.createWithValue(plan.fpsNum / plan.fpsDen);
    try { fr.ticksPerFrame = Number(plan.ticksPerFrame); } catch (e) { /* read-only on some builds; verified below */ }
    settings.setVideoFrameRate(fr);
    if (ppro.RectF && typeof settings.setVideoFrameRect === "function") {
      const r = new ppro.RectF();
      r.width = plan.width;
      r.height = plan.height;
      await settings.setVideoFrameRect(r);
    }
    txn(project, "AutoSync: sequence settings", () => [seq.createSetSettingsAction(settings)]);
    const check = (await seq.getSettings()).getVideoFrameRate();
    const got = BigInt(Math.round(Number(check.ticksPerFrame)));
    if (got !== plan.ticksPerFrame) {
      throw new Error(`New sequence runs at ${got} ticks/frame; expected ${plan.ticksPerFrame}. Placement stopped so no clip lands off-frame.`);
    }
    return seq;
  }

  // sequence.getProjectItem(): Promise<ProjectItem>; FolderItem.createMoveItemAction(item, newParent)
  async function moveToBin(project, projectItem, bin) {
    const pi = ppro.ProjectItem.cast(projectItem) || projectItem;
    const parent = pi.getParentBin();
    if (parent && parent.getId && parent.getId() === bin.getId()) return;
    txn(project, "AutoSync: organise bin", () => [parent.createMoveItemAction(pi, bin)]);
  }

  async function moveSequenceToBin(project, seq, bin) {
    if (typeof seq.getProjectItem !== "function") return;
    await moveToBin(project, await seq.getProjectItem(), bin);
  }

  /*
   * Source in/out marks. Insert/overwrite ignore marks set in the same transaction (notes item 6,
   * SECONDARY), so marks are set in their own transaction before placement and the user's
   * original marks are restored afterwards. createSetInOutPointsAction exists since 25.6.
   */
  function mediaTypeFor(item) {
    return item.hasVideo ? ppro.Constants.MediaType.VIDEO : ppro.Constants.MediaType.AUDIO;
  }

  async function readMarks(clip, mediaType) {
    const i = await clip.getInPoint(mediaType);
    const o = await clip.getOutPoint(mediaType);
    return { inTicks: i.ticks, outTicks: o.ticks };
  }

  async function setMarks(project, entries) {
    // entries: [{clip, inTicks (BigInt|string), outTicks}]
    if (!entries.length) return;
    txn(project, "AutoSync: source in/out", () => entries.map((e) =>
      e.clip.createSetInOutPointsAction(ppro.TickTime.createWithTicks(String(e.inTicks)), ppro.TickTime.createWithTicks(String(e.outTicks)))));
  }

  // VideoTrack/AudioTrack.getTrackItems(Constants.TrackItemType.CLIP, false) is sync; item getters async.
  async function readTrack(track, trackIndex) {
    const items = track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false) || [];
    // Read times into strings first: later awaits can invalidate transient objects (Syncitol, notes item 7).
    const raw = [];
    for (const it of items) {
      const s = (await it.getStartTime()).ticks;
      let inn = null;
      try { inn = (await it.getInPoint()).ticks; } catch (e) { inn = null; }
      raw.push({ it, startTicks: String(s), inTicks: inn == null ? null : String(inn) });
    }
    const out = [];
    for (const r of raw) {
      let pid = null;
      let name = "";
      try {
        const pi = await r.it.getProjectItem();
        pid = pi ? idOf(pi) : null;
      } catch (e) { pid = null; }
      try { name = await r.it.getName(); } catch (e) { name = ""; }
      out.push({ trackIndex, startTicks: r.startTicks, inTicks: r.inTicks, projectItemId: pid, name, item: r.it });
    }
    return out;
  }

  async function observe(seq) {
    const video = [];
    const audio = [];
    const mutedAudio = [];
    const vc = await seq.getVideoTrackCount();
    for (let i = 0; i < vc; i++) video.push(...(await readTrack(await seq.getVideoTrack(i), i)));
    const ac = await seq.getAudioTrackCount();
    for (let i = 0; i < ac; i++) {
      const t = await seq.getAudioTrack(i);
      audio.push(...(await readTrack(t, i)));
      try { mutedAudio.push(await t.isMuted()); } catch (e) { mutedAudio.push(null); }
    }
    return { video, audio, mutedAudio };
  }

  // AudioTrack.setMute(bool): Promise<boolean> -- a direct call, not an Action (notes item 7).
  async function muteScratch(seq, plan) {
    const muted = [];
    if (!plan.muteScratch) return muted;
    const ac = await seq.getAudioTrackCount();
    for (let i = 1; i <= plan.scratchTracks && i < ac; i++) {
      const t = await seq.getAudioTrack(i);
      await t.setMute(true);
      muted.push(i + 1);
    }
    return muted;
  }

  // project.openSequence / setActiveSequence / sequence.setPlayerPosition (notes item 9)
  async function openSequence(project, seq, ticks) {
    await project.openSequence(seq);
    await project.setActiveSequence(seq);
    await seq.setPlayerPosition(ppro.TickTime.createWithTicks(String(ticks || 0n)));
  }

  async function findSequence(project, guidString) {
    for (const s of await project.getSequences()) {
      if (String(s.guid) === guidString || (s.guid && s.guid.toString && s.guid.toString() === guidString)) return s;
    }
    return null;
  }

  // ProjectUtils.getSelection(project): Promise<ProjectItemSelection>; selection.getItems() (notes 13.1)
  // A selected bin contributes every clip inside it (sub-bins included). Returns the media paths
  // and how many items Premiere reported as selected, so an empty result can say which case it is.
  async function selectedMediaPaths(project) {
    const sel = await ppro.ProjectUtils.getSelection(project);
    const items = sel ? await sel.getItems() : [];
    const found = new Map();
    for (const it of items) {
      const clip = ppro.ClipProjectItem.cast(it);
      if (clip) {
        let p = "";
        try { p = await clip.getMediaFilePath(); } catch (e) { p = ""; }
        if (p && !found.has(core.normPath(p))) found.set(core.normPath(p), p);
        continue;
      }
      const f = ppro.FolderItem.cast(it);
      if (f) {
        for (const c of (await walkClips(f)).values()) {
          let p = "";
          try { p = await c.getMediaFilePath(); } catch (e) { p = ""; }
          if (p && !found.has(core.normPath(p))) found.set(core.normPath(p), p);
        }
      }
    }
    return { paths: [...found.values()], selected: items.length };
  }

  return {
    UserError, activeProject, txn, idOf, findBin, ensureBin, walkClips, importAndMap, createSequence,
    moveToBin, moveSequenceToBin, mediaTypeFor, readMarks, setMarks, readTrack, observe, muteScratch,
    openSequence, findSequence, selectedMediaPaths,
  };
}

module.exports = { createPremiere, UserError };
