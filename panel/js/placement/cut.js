"use strict";
/*
 * First cut / ad cut placed directly through the UXP API: the route used when Premiere does not
 * create a sequence from the imported XML (project.importFiles + XML is undocumented in UXP,
 * notes item 4). Payload: export/uxp_payload.py.
 *
 * - Each shot: its source in/out marks are set in their own transaction (insert/overwrite ignore
 *   marks set in the same transaction, notes item 6), then SequenceEditor.createOverwriteItemAction
 *   at the shot's start (VERIFIED). Shots of one take reuse the clip with different marks, so marks
 *   are set before every shot and the user's original marks are restored at the end.
 * - Markers: Markers.getMarkers(sequence) + createAddMarkerAction(name, type, start, duration,
 *   comment) (VERIFIED, 25.6); colours by createSetColorByIndexAction (VERIFIED) when available.
 * - Transitions: TransitionFactory + createAddVideoTransitionAction (VERIFIED, 25.6). Match names
 *   are looked up at runtime ("Cross Dissolve", "Dip to White"); transitions cannot be read back
 *   (Adobe staff, notes) so the report says they were requested, not verified.
 * - Camera sound: music video cuts mute A2 (setMute); ad picture-only shots disable their sound
 *   item (createSetDisabledAction, VERIFIED 25.6) when the API has it.
 * - Every shot is read back (start, in point, project item) before success is reported.
 */

function createCutPlacer(ppro, core, pr, uxpAdapter) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const tick = (t) => ppro.TickTime.createWithTicks(String(t));

  async function addMarkers(project, seq, markers, report) {
    if (!markers.length) return;
    if (!ppro.Markers || typeof ppro.Markers.getMarkers !== "function") {
      report.notes.push("This Premiere has no marker API: section/shot markers were not added (the XML route adds them).");
      return;
    }
    const m = await ppro.Markers.getMarkers(seq);
    const type = (ppro.Marker && ppro.Marker.MARKER_TYPE_COMMENT) || "Comment";
    pr.txn(project, "AutoSync: markers", () => markers.map((k) =>
      m.createAddMarkerAction(k.name, type, tick(k.startTicks), tick(k.durationTicks), k.comment || "")));
    report.markers = markers.length;
    const colours = ppro.Constants && ppro.Constants.MarkerColor;
    if (!colours || typeof m.getMarkers !== "function") return;
    try {
      const placed = await m.getMarkers();
      const todo = []; // actions are created inside lockedAccess (enforced since 26.3)
      for (const k of markers) {
        const key = String(k.colour || "").toUpperCase();
        const idx = colours[key] != null ? colours[key] : (key === "MAGENTA" ? colours.MAGNETA : undefined);
        if (idx == null) continue;
        for (const pm of placed) {
          const st = await pm.getStart();
          if (String(st.ticks) === String(k.startTicks) && (await pm.getName()) === k.name && typeof pm.createSetColorByIndexAction === "function") {
            todo.push([pm, idx]);
            break;
          }
        }
      }
      if (todo.length) pr.txn(project, "AutoSync: marker colours", () => todo.map(([pm, idx]) => pm.createSetColorByIndexAction(idx)));
    } catch (e) {
      report.notes.push(`Marker colours not set: ${e && e.message ? e.message : e}`);
    }
  }

  async function addTransitions(project, seq, transitions, tpf, report) {
    if (!transitions.length) return;
    if (!ppro.TransitionFactory || typeof ppro.TransitionFactory.getVideoTransitionMatchNames !== "function") {
      report.notes.push("This Premiere has no transition API: dissolves/flashes were not added (the XML route adds them).");
      return;
    }
    const names = await ppro.TransitionFactory.getVideoTransitionMatchNames();
    const pick = (re) => names.find((n) => re.test(n) && !/legacy/i.test(n)) || names.find((n) => re.test(n));
    const byKind = { dissolve: pick(/cross\s*dissolve/i), dip_white: pick(/dip\s*to\s*white/i) };
    const v1 = await seq.getVideoTrack(0);
    const items = await pr.readTrack(v1, 0);
    let added = 0;
    for (const t of transitions) {
      const name = byKind[t.kind];
      const target = items.find((x) => x.startTicks === String(t.atTicks));
      if (!name || !target) continue;
      try {
        const tr = await ppro.TransitionFactory.createVideoTransition(name);
        const opts = new ppro.AddTransitionOptions().setApplyToStart(true).setDuration(tick(BigInt(t.frames) * tpf));
        pr.txn(project, "AutoSync: transition", () => [target.item.createAddVideoTransitionAction(tr, opts)]);
        added += 1;
      } catch (e) {
        report.notes.push(`Transition at ${t.atTicks} not added: ${e && e.message ? e.message : e}`);
      }
    }
    report.transitions = { requested: transitions.length, added, verified: false };
    if (added) report.notes.push(`${added} transition(s) added; Premiere's API cannot read transitions back, so check them by eye.`);
  }

  async function place(payload, opts) {
    const o = Object.assign({ onProgress: () => {}, batchSize: 8 }, opts || {});
    const t0 = Date.now();
    const report = { adapter: "uxp-cut", sequenceName: payload.sequenceName, planned: payload.video.length + payload.audio.length,
      placed: 0, errors: [], mismatches: [], notes: (payload.notes || []).slice() };
    const tpf = BigInt(payload.ticksPerFrame);
    const project = await pr.activeProject();
    const root = await project.getRootItem();
    const bin = await pr.ensureBin(project, root, "AutoSync");
    const paths = [...new Set([...payload.audio, ...payload.video].map((x) => x.path))];
    o.onProgress({ phase: "importing media", done: 0, total: paths.length });
    const imp = await pr.importAndMap(project, paths, bin);
    for (const p of imp.notFound) report.errors.push({ name: core.baseName(p), error: "Premiere could not import this file" });

    const seqPlan = { sequenceName: payload.sequenceName, fpsNum: payload.fpsNum, fpsDen: payload.fpsDen, ticksPerFrame: tpf,
      width: payload.width, height: payload.height };
    const seq = await pr.step("Creating the sequence", () => pr.createSequence(project, seqPlan));
    await pr.step("Moving the sequence into its bin", () => pr.moveSequenceToBin(project, seq, () => pr.binAt(project, ["AutoSync"])));
    const editor = ppro.SequenceEditor.getEditor(seq);
    let far = 0n;
    for (const x of [...payload.video, ...payload.audio]) far = far > BigInt(x.startTicks) + BigInt(x.outTicks) ? far : BigInt(x.startTicks) + BigInt(x.outTicks);
    const plan = { farTicks: far + 3600n * 254016000000n };

    const clips = new Map();
    const originals = [];
    for (const p of paths) {
      const c = imp.map.get(core.normPath(p));
      if (!c) continue;
      clips.set(core.normPath(p), c);
      for (const mt of [ppro.Constants.MediaType.VIDEO, ppro.Constants.MediaType.AUDIO]) {
        try { originals.push(Object.assign({ clip: c }, await pr.readMarks(c, mt))); break; } catch (e) { /* try the other media type */ }
      }
    }
    const placedVideo = [];
    const all = [...payload.audio.map((x) => Object.assign({ audioOnly: true }, x)), ...payload.video];
    try {
      for (let i = 0; i < all.length; i++) {
        const x = all[i];
        const clip = clips.get(core.normPath(x.path));
        if (!clip) continue;
        try {
          await pr.setMarks(project, [{ clip, inTicks: x.inTicks, outTicks: x.outTicks }]);
          const it = { name: x.name, vIdx: 0, aIdx: x.audioTrack, startTicks: BigInt(x.startTicks), hasAudio: true };
          await uxpAdapter.placeItem(project, seq, editor, plan, it, clip, pr.idOf(clip));
          report.placed += 1;
          if (!x.audioOnly) placedVideo.push({ x, pid: pr.idOf(clip) });
        } catch (e) {
          report.errors.push({ name: x.name, error: e && e.message ? e.message : String(e) });
        }
        if ((i + 1) % o.batchSize === 0 || i === all.length - 1) {
          o.onProgress({ phase: "placing", done: i + 1, total: all.length });
          await sleep(0);
        }
      }
    } finally {
      try { await pr.setMarks(project, originals.map((m) => ({ clip: m.clip, inTicks: m.inTicks, outTicks: m.outTicks }))); } catch (e) {
        report.notes.push(`Could not restore source in/out marks: ${e.message}`);
      }
    }

    // Camera sound: mute whole tracks (music video) or disable picture-only shots' sound (ad).
    for (const ti of payload.muteAudioTracks || []) {
      try { const t = await seq.getAudioTrack(ti); if (t) { await t.setMute(true); report.notes.push(`Camera sound on A${ti + 1} muted.`); } } catch (e) { /* reported below */ }
    }
    const silent = placedVideo.filter((p) => p.x.keepAudio === false && p.x.audioTrack != null && !(payload.muteAudioTracks || []).includes(p.x.audioTrack));
    if (silent.length) {
      const t = await seq.getAudioTrack(silent[0].x.audioTrack);
      const items = t ? await pr.readTrack(t, silent[0].x.audioTrack) : [];
      const targets = [];
      for (const p of silent) {
        const a = items.find((y) => y.startTicks === p.x.startTicks && y.projectItemId === p.pid);
        if (a && typeof a.item.createSetDisabledAction === "function") targets.push(a.item);
      }
      if (targets.length) pr.txn(project, "AutoSync: picture-only sound off", () => targets.map((it) => it.createSetDisabledAction(true)));
      if (targets.length < silent.length) report.notes.push(`${silent.length - targets.length} picture-only shot(s) keep their camera sound on A${silent[0].x.audioTrack + 1}: turn it down by hand.`);
    }
    await addMarkers(project, seq, payload.markers || [], report);
    await addTransitions(project, seq, payload.transitions || [], tpf, report);

    // Readback: every shot on V1 at its start with its in point and its clip.
    o.onProgress({ phase: "verifying", done: all.length, total: all.length });
    const v1 = await pr.readTrack(await seq.getVideoTrack(0), 0);
    for (const p of placedVideo) {
      const got = v1.find((y) => y.startTicks === p.x.startTicks && y.projectItemId === p.pid);
      if (!got) { report.mismatches.push({ name: p.x.name, kind: "missing", expected: p.x.startTicks }); continue; }
      if (got.inTicks != null && BigInt(got.inTicks) !== BigInt(p.x.inTicks)) {
        report.mismatches.push({ name: p.x.name, kind: "in_point", expected: p.x.inTicks, actual: got.inTicks });
      }
    }
    await pr.openSequence(project, seq, 0n);
    report.sequenceGuid = String(seq.guid);
    report.totalMs = Date.now() - t0;
    report.ok = report.errors.length === 0 && report.mismatches.length === 0 && report.placed === report.planned;
    return report;
  }

  return { place };
}

module.exports = { createCutPlacer };
