"use strict";
/*
 * First cut (docs/EDIT_DESIGN.md). The helper plans the edit and writes an FCP7 XML sequence
 * (export/edit_xml.py); the panel imports it into the project and opens it.
 *
 * project.importFiles creating a sequence from an .xml through UXP is NOT documented
 * (docs/PREMIERE_API_NOTES.md item 4), so a new sequence is detected by diffing
 * project.getSequences(). When none appears the panel says so and gives the manual route
 * (File > Import the XML). Nothing is reported as imported unless the sequence is found.
 */

function createFirstCut(ppro, pr, api, sleepFn, cutPlacer) {
  const sleep = sleepFn || ((ms) => new Promise((r) => setTimeout(r, ms)));

  // XML import first; when Premiere builds no sequence from it, place the cut directly (UXP).
  async function bring(project, out) {
    const imp = await importXml(project, out.xml_path, out.sequence_name);
    if (imp.seq) {
      await pr.openSequence(project, imp.seq, 0n);
      return { imported: true, method: "xml", sequenceGuid: String(imp.seq.guid), notes: imp.notes, placement: null };
    }
    if (cutPlacer && out.uxp) {
      const rep = await cutPlacer.place(out.uxp);
      return { imported: rep.ok, method: "uxp", sequenceGuid: rep.sequenceGuid || null,
        notes: imp.notes.concat(["Premiere did not build a sequence from the XML; the cut was placed directly instead."], rep.notes || []),
        placement: { placed: rep.placed, planned: rep.planned, mismatches: rep.mismatches, errors: rep.errors, markers: rep.markers || 0,
          transitions: rep.transitions || null } };
    }
    return { imported: false, method: null, sequenceGuid: null, notes: imp.notes, placement: null };
  }

  async function autosyncBin(project) {
    const root = await project.getRootItem();
    return pr.ensureBin(project, root, "AutoSync");
  }

  async function importXml(project, xmlPath, name) {
    const bin = await autosyncBin(project);
    const before = new Set((await project.getSequences()).map((s) => String(s.guid)));
    const notes = [];
    let returned = false;
    try {
      returned = await project.importFiles([xmlPath], true, ppro.ProjectItem.cast(bin) || bin, false);
    } catch (e) {
      notes.push(`importFiles threw: ${e && e.message ? e.message : e}`);
    }
    const fresh = (await project.getSequences()).filter((s) => !before.has(String(s.guid)));
    const seq = fresh.find((s) => s.name === name) || fresh[0] || null;
    return { seq, returned, notes };
  }

  async function make(jobId, opts) {
    if (!jobId) throw new Error("Analyse the clips first.");
    const out = await api.cut(jobId, Object.assign({}, opts, { preview: false }));
    const project = await pr.activeProject();
    const b = await bring(project, out);
    const versions = [];
    for (const v of out.versions || []) {  // social versions: XML route only (reframing is a Motion keyframe in the XML)
      const imp = await importXml(project, v.xml_path, v.sequence_name);
      versions.push(Object.assign({}, v, { imported: Boolean(imp.seq) }));
    }
    if (versions.length && b.sequenceGuid) {
      const seq = await pr.findSequence(project, b.sequenceGuid);
      if (seq) await pr.openSequence(project, seq, 0n);
    }
    return Object.assign({}, out, b, { versions });
  }

  async function preview(jobId, opts, onProgress) {
    if (!jobId) throw new Error("Analyse the clips first.");
    const out = await api.cut(jobId, Object.assign({}, opts, { preview: true }));
    let st = out.preview;
    while (st && st.state === "running") {
      if (onProgress) onProgress(st);
      await sleep(1000);
      st = await api.cutPreview(jobId);
    }
    if (!st || st.state !== "done") throw new Error(`The preview could not be rendered: ${st && st.error ? st.error : "no status from the helper"}`);
    const project = await pr.activeProject();
    const bin = await autosyncBin(project);
    let imported = false;
    let note = null;
    try {
      imported = await project.importFiles([st.path], true, ppro.ProjectItem.cast(bin) || bin, false);
    } catch (e) {
      note = `Premiere did not import the preview: ${e && e.message ? e.message : e}`;
    }
    return { path: st.path, imported: Boolean(imported), note, sequenceName: out.sequence_name };
  }

  // Shot analysis runs in the helper (faces, framing, sharpness, exposure, setups, colour).
  async function analyseShots(jobId, onProgress) {
    if (!jobId) throw new Error("Analyse the clips first.");
    let st = await api.shots(jobId, { faces: true });
    while (st && st.state === "running") {
      if (onProgress) onProgress(st);
      await sleep(1000);
      st = await api.shotsStatus(jobId);
    }
    if (!st || st.state !== "done") throw new Error(`Shot analysis failed: ${st && st.error ? st.error : "no status from the helper"}`);
    return st;
  }

  // Ad first cut: the helper reads the script, matches the takes and writes the XML; the panel
  // imports and opens it the same way as the music video cut.
  async function makeAd(req, onProgress) {
    let st = await api.adCreate(req);
    while (st && (st.state === "running" || st.state === "queued")) {
      if (onProgress) onProgress(st.progress || {});
      await sleep(1000);
      st = await api.adJob(st.id);
    }
    if (!st || st.state !== "done") throw new Error(`The ad cut could not be built: ${st && st.error ? st.error : "no status from the helper"}`);
    const r = st.result;
    const project = await pr.activeProject();
    const b = await bring(project, r);
    const versions = [];
    for (const v of r.versions || []) {  // cutdowns: each its own sequence
      const vb = await bring(project, v);
      versions.push(Object.assign({}, v, vb, { uxp: undefined }));
    }
    const aspects = [];
    for (const v of r.aspect_versions || []) {  // social versions: XML route (framing is Basic Motion in the XML)
      const imp = await importXml(project, v.xml_path, v.sequence_name);
      aspects.push(Object.assign({}, v, { imported: Boolean(imp.seq) }));
    }
    if (b.sequenceGuid) {  // leave the main cut open
      const seq = await pr.findSequence(project, b.sequenceGuid);
      if (seq) await pr.openSequence(project, seq, 0n);
    }
    return Object.assign({ jobId: st.id }, r, b, { versions, aspect_versions: aspects });
  }

  async function adPreview(jobId, width, onProgress, aspect) {
    let st = await api.adPreviewStart(jobId, width, aspect);
    while (st && st.state === "running") {
      if (onProgress) onProgress(st);
      await sleep(1000);
      st = await api.adPreviewStatus(jobId);
    }
    if (!st || st.state !== "done") throw new Error(`The ad preview could not be rendered: ${st && st.error ? st.error : "no status from the helper"}`);
    const project = await pr.activeProject();
    const bin = await autosyncBin(project);
    let imported = false;
    let note = null;
    try {
      imported = await project.importFiles([st.path], true, ppro.ProjectItem.cast(bin) || bin, false);
    } catch (e) {
      note = `Premiere did not import the preview: ${e && e.message ? e.message : e}`;
    }
    return { path: st.path, imported: Boolean(imported), note };
  }

  return { make, preview, importXml, analyseShots, makeAd, adPreview };
}

module.exports = { createFirstCut };
