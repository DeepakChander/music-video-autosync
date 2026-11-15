"use strict";
/* Steps shared by both placement adapters: bins, binning of clips that are not placed,
 * readback verification. Never touches an existing sequence. */

function createCommon(ppro, core, pr) {
  // Bins: AutoSync / <run>, AutoSync / Unsynced, AutoSync / Needs review (spec section 7).
  // Created here; every later use calls bins.get(key) for a fresh FolderItem (premiere.js binAt).
  async function prepareBins(project, plan) {
    const paths = { autosync: ["AutoSync"], runBin: ["AutoSync", plan.sequenceName],
      unsyncedBin: ["AutoSync", "Unsynced"], reviewBin: ["AutoSync", "Needs review"] };
    for (const k of Object.keys(paths)) await pr.binAt(project, paths[k]);
    return { paths, get: (key) => pr.binAt(project, paths[key]) };
  }

  // UNSYNCED clips go to AutoSync / Unsynced; LOW clips that were not placed go to
  // AutoSync / Needs review. Files Premiere cannot import (e.g. corrupt) are listed, not hidden.
  async function binLeftovers(project, plan, bins) {
    const out = { unsynced: 0, review: 0, notImported: [] };
    for (const [list, binKey, key] of [[plan.unsynced, "unsyncedBin", "unsynced"], [plan.review, "reviewBin", "review"]]) {
      if (!list.length) continue;
      const imp = await pr.importAndMap(project, list.map((c) => c.path), await bins.get(binKey));
      for (const c of list) {
        const clip = imp.map.get(core.normPath(c.path));
        if (!clip) { out.notImported.push(c.name); continue; }
        await pr.moveToBin(project, clip, () => bins.get(binKey));
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
