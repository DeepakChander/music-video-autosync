"use strict";
/*
 * Placement adapter A: UXP direct, through SequenceEditor actions in small batches.
 *
 * - Overwrite (never ripples) for clips whose tracks exist:
 *   SequenceEditor.createOverwriteItemAction(projectItem, TickTime, videoTrackIndex, audioTrackIndex)
 *   https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequenceeditor/
 * - New tracks: only insert is documented to create a track when the index equals the track count
 *   (notes item 5). The first clip of a new track is inserted at a staging point beyond all
 *   content (nothing there to ripple) and moved to its position with delta moves:
 *   VideoClipTrackItem/AudioClipTrackItem.createMoveAction(TickTime delta). Moves do not carry
 *   linked items, so the video and the scratch audio are moved by the same delta (spec section 7).
 * - Source in/out marks are set in their own transaction first and restored afterwards.
 * - Every result is read back; success is reported only when readback matches the plan.
 */

function createUxpAdapter(ppro, core, pr, common) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const tick = (t) => ppro.TickTime.createWithTicks(String(t));

  async function itemsAt(seq, ticks, pid) {
    const obs = await pr.observe(seq);
    return [...obs.video, ...obs.audio].filter((x) => x.projectItemId === pid && BigInt(x.startTicks) === BigInt(ticks));
  }

  // Place one plan item on an open sequence. Returns "overwrite" or "staged".
  async function placeItem(project, seq, editor, plan, it, clip, pid) {
    const pi = ppro.ProjectItem.cast(clip) || clip;
    const vc = await seq.getVideoTrackCount();
    const ac = await seq.getAudioTrackCount();
    const aIdx = Math.min(it.aIdx, ac);
    if (it.vIdx < vc && (!it.hasAudio || it.aIdx < ac)) {
      pr.txn(project, `AutoSync: place ${it.name}`, () => [editor.createOverwriteItemAction(pi, tick(it.startTicks), it.vIdx, aIdx)]);
      return "overwrite";
    }
    if (it.vIdx > vc) throw new Error(`video track V${it.vIdx + 1} requested but only ${vc} exist`);
    pr.txn(project, `AutoSync: new track for ${it.name}`, () => [editor.createInsertProjectItemAction(pi, tick(plan.farTicks), it.vIdx, aIdx, true)]);
    const staged = await itemsAt(seq, plan.farTicks, pid);
    if (!staged.length) throw new Error("inserted clip was not found at the staging point");
    const delta = it.startTicks - plan.farTicks;
    pr.txn(project, `AutoSync: move ${it.name}`, () => staged.map((x) => x.item.createMoveAction(tick(delta))));
    return "staged";
  }

  async function place(planIn, opts) {
    const o = Object.assign({ batchSize: 10, onProgress: () => {} }, opts || {});
    const t0 = Date.now();
    const report = { adapter: "uxp", sequenceName: planIn.sequenceName, planned: planIn.items.length, placed: 0,
      errors: planIn.errors.map((e) => Object.assign({}, e)), mismatches: [], notes: [], timings: {} };
    let plan = planIn;
    if (!plan.scratchTracks && plan.items.some((it) => it.hasAudioSource)) {
      // The UXP API has no video-only insert (notes item 5), so scratch audio cannot be left out
      // here. Keep it, mute it, and say so; the XML method can leave it out.
      plan = Object.assign({}, planIn, { items: planIn.items.map((it) => Object.assign({}, it, { hasAudio: it.hasAudioSource })),
        scratchTracks: planIn.videoTracks, muteScratch: true });
      report.notes.push("UXP direct cannot place video without its audio: scratch audio was kept and muted. Use XML import to leave it out.");
    }
    const project = await pr.activeProject();
    const bins = await common.prepareBins(project, plan);
    o.onProgress({ phase: "importing media", done: 0, total: plan.items.length });
    const imp = await pr.importAndMap(project, [plan.master.path, ...plan.items.map((i) => i.path)], bins.runBin);
    const masterClip = imp.map.get(core.normPath(plan.master.path));
    if (!masterClip) throw new pr.UserError(`Premiere could not import the master: ${plan.master.path}`);
    const idByClip = new Map();
    const clipFor = new Map();
    for (const it of plan.items) {
      const c = imp.map.get(core.normPath(it.path));
      if (!c) { report.errors.push({ clipId: it.clipId, name: it.name, error: "Premiere could not import this file" }); continue; }
      clipFor.set(it.clipId, c);
      idByClip.set(it.clipId, pr.idOf(c));
    }
    report.timings.importMs = Date.now() - t0;

    const seq = await pr.createSequence(project, plan);
    await pr.moveSequenceToBin(project, seq, bins.runBin);
    const editor = ppro.SequenceEditor.getEditor(seq);
    const items = plan.items.filter((it) => clipFor.has(it.clipId));

    const originals = [];
    for (const it of items) {
      const clip = clipFor.get(it.clipId);
      originals.push(Object.assign({ clip }, await pr.readMarks(clip, pr.mediaTypeFor(it))));
    }
    await pr.setMarks(project, items.map((it) => ({ clip: clipFor.get(it.clipId), inTicks: it.inTicks, outTicks: it.durationTicks })));

    const tPlace = Date.now();
    let maxBlock = 0;
    let blockStart = Date.now();
    let staged = 0;
    try {
      pr.txn(project, "AutoSync: master audio", () => [editor.createOverwriteItemAction(ppro.ProjectItem.cast(masterClip) || masterClip, tick(0n), 0, 0)]);
      for (let i = 0; i < items.length; i++) {
        const it = items[i];
        try {
          const how = await placeItem(project, seq, editor, plan, it, clipFor.get(it.clipId), idByClip.get(it.clipId));
          if (how === "staged") staged += 1;
          report.placed += 1;
        } catch (e) {
          report.errors.push({ clipId: it.clipId, name: it.name, error: e && e.message ? e.message : String(e) });
        }
        if ((i + 1) % o.batchSize === 0 || i === items.length - 1) {
          maxBlock = Math.max(maxBlock, Date.now() - blockStart);
          o.onProgress({ phase: "placing", done: i + 1, total: items.length });
          await sleep(0); // yield to Premiere's UI between batches
          blockStart = Date.now();
        }
      }
    } finally {
      try {
        await pr.setMarks(project, originals.map((x) => ({ clip: x.clip, inTicks: x.inTicks, outTicks: x.outTicks })));
      } catch (e) {
        report.notes.push(`Could not restore source in/out marks: ${e.message}`);
      }
    }
    report.timings.placeMs = Date.now() - tPlace;
    report.timings.maxBlockMs = maxBlock;
    report.timings.stagedTracks = staged;

    o.onProgress({ phase: "verifying", done: items.length, total: items.length });
    report.mutedTracks = await pr.muteScratch(seq, plan);
    report.binned = await common.binLeftovers(project, plan, bins);
    const rb = await common.verify(seq, plan, idByClip, pr.idOf(masterClip));
    report.mismatches = rb.mismatches;
    await pr.openSequence(project, seq, 0n);
    report.sequenceGuid = String(seq.guid);
    report.idByClip = Object.fromEntries(idByClip);
    report.totalMs = Date.now() - t0;
    report.ok = report.errors.length === 0 && rb.ok && report.placed === plan.items.length;
    return report;
  }

  /*
   * Manual correction (spec section 7): the override is already saved through the helper; move
   * only that clip. A clip already in the draft is moved by delta (video and scratch audio
   * together) when it stays on its track; a clip not yet in the draft is placed.
   */
  async function applyOverride(draft, plan, it) {
    const project = await pr.activeProject();
    const seq = await pr.findSequence(project, draft.sequenceGuid);
    if (!seq) throw new pr.UserError("The draft sequence is no longer in the project. Generate a new draft.");
    const bins = await common.prepareBins(project, Object.assign({}, plan, { sequenceName: draft.sequenceName }));
    const imp = await pr.importAndMap(project, [it.path], bins.runBin);
    const clip = imp.map.get(core.normPath(it.path));
    if (!clip) throw new pr.UserError(`Premiere could not import ${it.path}`);
    const pid = pr.idOf(clip);
    const obs = await pr.observe(seq);
    const current = [...obs.video, ...obs.audio.filter((a) => a.trackIndex > 0)].filter((x) => x.projectItemId === pid);
    if (current.length) {
      const v = current.find((x) => obs.video.includes(x));
      if (v && v.trackIndex !== it.vIdx) {
        throw new pr.UserError(`The new position needs track V${it.vIdx + 1} but the clip is on V${v.trackIndex + 1}. The correction is saved; generate a new draft to apply it.`);
      }
      const delta = it.startTicks - BigInt((v || current[0]).startTicks);
      if (delta !== 0n) pr.txn(project, `AutoSync: correct ${it.name}`, () => current.map((x) => x.item.createMoveAction(tick(delta))));
    } else {
      const editor = ppro.SequenceEditor.getEditor(seq);
      const orig = await pr.readMarks(clip, pr.mediaTypeFor(it));
      await pr.setMarks(project, [{ clip, inTicks: it.inTicks, outTicks: it.durationTicks }]);
      try {
        await placeItem(project, seq, editor, plan, it, clip, pid);
      } finally {
        await pr.setMarks(project, [{ clip, inTicks: orig.inTicks, outTicks: orig.outTicks }]);
      }
      if (plan.muteScratch && it.hasAudio) {
        const t = await seq.getAudioTrack(Math.min(it.aIdx, (await seq.getAudioTrackCount()) - 1));
        if (t) await t.setMute(true);
      }
    }
    // read back just this clip
    const after = await pr.observe(seq);
    const v = after.video.find((x) => x.projectItemId === pid);
    const ok = Boolean(v) && BigInt(v.startTicks) === it.startTicks && v.trackIndex === it.vIdx;
    return { ok, pid, actual: v ? v.startTicks : null, expected: String(it.startTicks) };
  }

  return { name: "uxp", placeItem, place, applyOverride };
}

module.exports = { createUxpAdapter };
