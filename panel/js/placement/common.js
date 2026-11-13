"use strict";
/* Steps shared by both placement adapters: bins, binning of clips that are not placed,
 * readback verification. Never touches an existing sequence. */

function createCommon(ppro, core, pr) {
  // Bins: AutoSync / <run>, AutoSync / Unsynced, AutoSync / Needs review (spec section 7).
  async function prepareBins(project, plan) {
    const root = await project.getRootItem();
    const autosync = await pr.ensureBin(project, root, "AutoSync");
    const runBin = await pr.ensureBin(project, autosync, plan.sequenceName);
    const unsyncedBin = await pr.ensureBin(project, autosync, "Unsynced");
    const reviewBin = await pr.ensureBin(project, autosync, "Needs review");
    return { autosync, runBin, unsyncedBin, reviewBin };
  }

  // UNSYNCED clips go to AutoSync / Unsynced; LOW clips that were not placed go to
  // AutoSync / Needs review. Files Premiere cannot import (e.g. corrupt) are listed, not hidden.
  async function binLeftovers(project, plan, bins) {
    const out = { unsynced: 0, review: 0, notImported: [] };
    for (const [list, bin, key] of [[plan.unsynced, bins.unsyncedBin, "unsynced"], [plan.review, bins.reviewBin, "review"]]) {
      if (!list.length) continue;
      const imp = await pr.importAndMap(project, list.map((c) => c.path), bin);
      for (const c of list) {
        const clip = imp.map.get(core.normPath(c.path));
        if (!clip) { out.notImported.push(c.name); continue; }
        await pr.moveToBin(project, clip, bin);
        out[key] += 1;
      }
    }
    return out;
  }

  async function verify(seq, plan, idByClip, masterId) {
    const obs = await pr.observe(seq);
    const strip = (x) => ({ trackIndex: x.trackIndex, startTicks: x.startTicks, inTicks: x.inTicks, projectItemId: x.projectItemId, name: x.name });
    const observed = { video: obs.video.map(strip), audio: obs.audio.map(strip), mutedAudio: obs.mutedAudio };
    return core.compareReadback(plan, observed, idByClip, masterId);
  }

  function stamp(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  }

  function sequenceName(masterPath, date) {
    const song = core.baseName(masterPath).replace(/\.[^.]+$/, "");
    return `AutoSync - ${song} - ${stamp(date || new Date())}`;
  }

  return { prepareBins, binLeftovers, verify, sequenceName, stamp };
}

module.exports = { createCommon };
