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

function createFirstCut(ppro, pr, api, sleepFn) {
  const sleep = sleepFn || ((ms) => new Promise((r) => setTimeout(r, ms)));

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
    const imp = await importXml(project, out.xml_path, out.sequence_name);
    if (imp.seq) await pr.openSequence(project, imp.seq, 0n);
    return Object.assign({}, out, {
      imported: Boolean(imp.seq),
      sequenceGuid: imp.seq ? String(imp.seq.guid) : null,
      importReturned: imp.returned,
      notes: imp.notes,
    });
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

  return { make, preview, importXml };
}

module.exports = { createFirstCut };
