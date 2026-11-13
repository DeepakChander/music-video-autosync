"use strict";
/*
 * Placement self-test = the spec's Phase 0 spikes, run on the client's own Premiere:
 *   (a) the panel reaches the helper (GET /health);
 *   (b) adapter A places N generated clips in batches -- time and longest UI block measured;
 *   (c) adapter B imports the helper's FCP7 XML -- positions read back.
 * Both adapters place the SAME engine result of takes with known truth, each into its own new
 * sequence; every item is read back. The default adapter becomes the one that passed readback
 * (faster wins if both did) -- docs/DECISIONS.md D-002. The report is saved by the helper.
 */

function createSelfTest(core, api, adapters, common, storage) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function waitJob(jobId, onStatus) {
    for (;;) {
      const j = await api.job(jobId);
      onStatus({ phase: j.progress.phase, done: j.progress.done, total: j.progress.total });
      if (j.state === "done") return j;
      if (["failed", "cancelled", "interrupted"].includes(j.state)) {
        throw new Error(`Self-test job ${j.state}: ${j.error ? j.error.message : ""}`);
      }
      await sleep(700);
    }
  }

  function summarize(r) {
    return {
      adapter: r.adapter, ok: Boolean(r.ok), planned: r.planned, placed: r.placed || 0,
      mismatches: (r.mismatches || []).length, errors: (r.errors || []).map((e) => e.error || String(e)),
      needsManualImport: Boolean(r.needsManualImport), totalMs: r.totalMs || 0,
      maxBlockMs: (r.timings && r.timings.maxBlockMs) || null, sequenceName: r.sequenceName,
      firstMismatches: (r.mismatches || []).slice(0, 5),
    };
  }

  async function run(opts) {
    const o = Object.assign({ count: 24, onStatus: () => {}, sequence: { fps_num: 24000, fps_den: 1001, width: 1920, height: 1080 } }, opts || {});
    const out = { startedAt: new Date().toISOString(), count: o.count, spikes: {} };
    o.onStatus({ phase: "spike (a): contacting helper" });
    const health = await api.health();
    out.spikes.a = { ok: health.status === "ok", version: health.version, ffmpeg: health.ffmpeg_found };
    if (!health.ffmpeg_found) throw new Error("The helper cannot find FFmpeg; reinstall the helper.");

    o.onStatus({ phase: "generating test takes" });
    const { job_id: jobId } = await api.selftest(o.count, o.sequence);
    await waitJob(jobId, o.onStatus);
    const result = await api.result(jobId);
    const acc = await api.accuracy(jobId);
    out.jobId = jobId;
    out.engine = { gateFailures: acc.gate_failures, withinOneFramePct: acc.metrics.position_within_1_frame_pct,
      wrongHighMedium: acc.metrics.wrong_HIGH_or_MEDIUM, clips: result.clips.length };

    const reports = [];
    const stamp = common.stamp(new Date());
    for (const name of ["uxp", "xml"]) {
      const plan = core.buildPlan(result, { sequenceName: `AutoSync Self-test ${name.toUpperCase()} - ${stamp}` });
      o.onStatus({ phase: `spike (${name === "uxp" ? "b" : "c"}): placing ${plan.items.length} clips with ${name === "uxp" ? "UXP direct" : "XML import"}` });
      const t0 = Date.now();
      try {
        reports.push(await adapters[name].place(plan, { jobId, onProgress: (p) => o.onStatus({ phase: `${name}: ${p.phase}`, done: p.done, total: p.total }) }));
      } catch (e) {
        reports.push({ adapter: name, ok: false, planned: plan.items.length, errors: [{ error: e && e.message ? e.message : String(e) }], totalMs: Date.now() - t0 });
      }
    }
    out.spikes.b = summarize(reports[0]);
    out.spikes.c = summarize(reports[1]);
    out.defaultAdapter = core.pickDefaultAdapter(reports);
    out.finishedAt = new Date().toISOString();
    try {
      storage.setItem("autosync.selftest", JSON.stringify(out));
      if (out.defaultAdapter) storage.setItem("autosync.defaultAdapter", out.defaultAdapter);
    } catch (e) { /* storage full: the helper copy below is the record */ }
    try {
      out.savedTo = (await api.saveReport(jobId, "selftest_report", out)).path;
    } catch (e) {
      out.saveError = e.message;
    }
    return out;
  }

  function last() {
    try { return JSON.parse(storage.getItem("autosync.selftest") || "null"); } catch (e) { return null; }
  }

  return { run, last };
}

module.exports = { createSelfTest };
