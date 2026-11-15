"use strict";
/*
 * Placement adapter B: FCP7 XML (xmeml v5) written by the helper, imported into Premiere.
 *
 * project.importFiles(paths, suppressUI, targetBin, asNumberedStills): Promise<boolean> is
 * VERIFIED, but whether it creates a sequence from an .xml in UXP is NOT documented (notes item 4;
 * ExtendScript does). So the adapter detects a new sequence by diffing project.getSequences();
 * if none appears it says so and offers the manual path: File > Import, then "Verify imported
 * sequence" runs the same mute + readback on the active sequence. Never reports success without
 * readback.
 */

function createXmlAdapter(ppro, core, pr, common, api) {
  // XML in/out points are whole sequence frames; allow one frame of in-point rounding.
  function xmlPlan(plan) {
    return Object.assign({}, plan, {
      items: plan.items.map((it) => Object.assign({}, it, { inTolerance: it.inTolerance > plan.ticksPerFrame ? it.inTolerance : plan.ticksPerFrame })),
    });
  }

  async function finish(project, seq, plan, bins, report, t0) {
    const p = xmlPlan(plan);
    const settings = await seq.getSettings();
    if (settings && typeof settings.getVideoFrameRate === "function") {
      const got = BigInt(Math.round(Number(settings.getVideoFrameRate().ticksPerFrame)));
      if (got !== plan.ticksPerFrame) report.mismatches.push({ name: "sequence", kind: "frame_rate", expected: String(plan.ticksPerFrame), actual: String(got) });
    }
    const imp = await pr.step("Importing the media", async () =>
      pr.importAndMap(project, [plan.master.path, ...plan.items.map((i) => i.path)], await bins.get("runBin")));
    const masterClip = imp.map.get(core.normPath(plan.master.path));
    const idByClip = new Map();
    for (const it of plan.items) {
      const c = imp.map.get(core.normPath(it.path));
      if (c) idByClip.set(it.clipId, pr.idOf(c));
    }
    report.mutedTracks = await pr.step("Muting the camera sound", () => pr.muteScratch(seq, plan));
    report.binned = await pr.step("Putting unplaced clips in their bins", () => common.binLeftovers(project, plan, bins));
    const rb = await pr.step("Reading the timeline back", () => common.verify(seq, p, idByClip, masterClip ? pr.idOf(masterClip) : null));
    report.mismatches = report.mismatches.concat(rb.mismatches);
    report.placed = plan.items.length - rb.mismatches.filter((m) => m.kind === "video_missing" || m.kind === "not_in_project").length;
    await pr.openSequence(project, seq, 0n);
    report.sequenceGuid = String(seq.guid);
    report.idByClip = Object.fromEntries(idByClip);
    report.totalMs = Date.now() - t0;
    report.ok = report.errors.length === 0 && report.mismatches.length === 0;
    return report;
  }

  async function place(plan, opts) {
    const o = Object.assign({ onProgress: () => {} }, opts || {});
    if (!o.jobId) throw new Error("XML placement needs the analysis job id");
    const t0 = Date.now();
    const report = { adapter: "xml", sequenceName: plan.sequenceName, planned: plan.items.length, placed: 0,
      errors: plan.errors.map((e) => Object.assign({}, e)), mismatches: [], notes: [], timings: {} };
    const project = await pr.activeProject();
    const bins = await pr.step("Creating the AutoSync bins", () => common.prepareBins(project, plan));
    o.onProgress({ phase: "writing XML", done: 0, total: plan.items.length });
    const x = await api.exportXml(o.jobId, { sequence_name: plan.sequenceName, keep_scratch_audio: plan.scratchTracks > 0, place_low: plan.placeLow });
    report.xmlPath = x.path;
    const before = new Set((await project.getSequences()).map((s) => String(s.guid)));
    o.onProgress({ phase: "importing XML", done: 0, total: plan.items.length });
    const tImport = Date.now();
    let ok = false;
    try {
      const runBin = await bins.get("runBin");
      ok = await project.importFiles([x.path], true, ppro.ProjectItem.cast(runBin) || runBin, false);
    } catch (e) {
      report.notes.push(`importFiles threw: ${e && e.message ? e.message : e}`);
    }
    report.timings.importMs = Date.now() - tImport;
    const fresh = (await project.getSequences()).filter((s) => !before.has(String(s.guid)));
    const seq = fresh.find((s) => s.name === plan.sequenceName) || fresh[0];
    if (!seq) {
      report.needsManualImport = true;
      report.importReturned = ok;
      report.errors.push({ error: "Premiere did not create a sequence from the XML through the UXP API. Import the XML with File > Import, open the new sequence, then click 'Verify imported sequence'." });
      report.totalMs = Date.now() - t0;
      report.ok = false;
      return report;
    }
    o.onProgress({ phase: "verifying", done: plan.items.length, total: plan.items.length });
    return finish(project, seq, plan, bins, report, t0);
  }

  // Manual-import path: the user imported the XML and opened the sequence.
  async function verifyImported(plan) {
    const t0 = Date.now();
    const report = { adapter: "xml", manualImport: true, sequenceName: plan.sequenceName, planned: plan.items.length, placed: 0,
      errors: [], mismatches: [], notes: [], timings: {} };
    const project = await pr.activeProject();
    const seq = await project.getActiveSequence();
    if (!seq) throw new pr.UserError("Open the imported AutoSync sequence in the timeline first.");
    if (seq.name !== plan.sequenceName) throw new pr.UserError(`The active sequence is "${seq.name}", expected "${plan.sequenceName}".`);
    const bins = await common.prepareBins(project, plan);
    return finish(project, seq, plan, bins, report, t0);
  }

  return { name: "xml", place, verifyImported };
}

module.exports = { createXmlAdapter };
