"use strict";
/* UI wiring: every button in index.html has a handler, and each click performs its real call
 * (against recording fakes). Spec Phase 4 gate: "Every button triggers a real operation". */
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const path = require("path");
const core = require("../js/core.js");
const { createCommon } = require("../js/placement/common.js");
const { createView } = require("../js/ui/view.js");

const HTML = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const RESULT = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "sync_result.json"), "utf8"));
const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle() { for (let i = 0; i < 30; i++) await tick(); }

function fakeDom() {
  const els = new Map();
  for (const m of HTML.matchAll(/<(\w+)([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const [, tag, attrs, id] = m;
    const el = { id, tagName: tag.toUpperCase(), textContent: "", innerHTML: "", className: (/class="([^"]*)"/.exec(attrs) || [])[1] || "",
      disabled: /\bdisabled\b/.test(attrs), checked: /\bchecked\b/.test(attrs), value: "", style: {}, listeners: {},
      addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
      async fire(t, ev) { for (const f of this.listeners[t] || []) await f(ev || { target: this }); } };
    if (tag === "select") {
      const block = HTML.slice(m.index, HTML.indexOf("</select>", m.index));
      el.value = (/<option value="([^"]+)"/.exec(block) || [])[1] || "";
    }
    els.set(id, el);
  }
  return { els, getElementById: (id) => { if (!els.has(id)) els.set(id, { id, value: "", style: {}, addEventListener() {} }); return els.get(id); } };
}

function setup() {
  const current = JSON.parse(JSON.stringify(RESULT));
  const calls = [];
  const rec = (name, ret) => async (...a) => { calls.push([name, ...a]); return typeof ret === "function" ? ret(...a) : ret; };
  let jobPolls = 0;
  const api = {
    health: rec("health", { status: "ok", ffmpeg_found: true, version: "0.1.0", engine_version: "0.1.0", cache_dir: "C:/c" }),
    master: rec("master", { duration_s: 214.3, sample_rate: 48000, peaks: [0.1, 0.5, 1] }),
    probe: rec("probe", (paths) => ({ files: paths.map((p) => ({ path: p, ok: true, has_audio: true, duration_s: 10 })), total_duration_s: 10 * paths.length, count: paths.length })),
    createJob: rec("createJob", { job_id: "J1" }),
    job: rec("job", () => ({ id: "J1", state: ++jobPolls > 1 ? "done" : "running", progress: { phase: "analysing", done: 1, total: 2, HIGH: 1, MEDIUM: 0, LOW: 0, UNSYNCED: 1 } })),
    result: rec("result", () => current),
    cancel: rec("cancel", {}), resume: rec("resume", {}),
    // behaves like the helper: snaps to a frame, stores the override, engine fields untouched
    override: rec("override", (jobId, clipId, s) => {
      const c = current.clips.find((x) => x.clip_id === clipId);
      const tpf = core.ticksPerFrame(current.sequence.fps_num, current.sequence.fps_den);
      c.override = s == null ? null : { song_position_s: s, song_position_ticks: String(BigInt(Math.round(Number(core.secondsToTicks(s)) / Number(tpf))) * tpf), by: "editor", at: "now", video_track: c.video_track || 1 };
      return { clip: c };
    }),
    saveReport: rec("saveReport", { path: "C:/r.json" }),
    setToken: (t) => calls.push(["setToken", t]),
  };
  const seq = { guid: "G1" };
  const pr = {
    activeProject: rec("activeProject", {}),
    selectedMediaPaths: rec("selectedMediaPaths", ["D:/shoot/takes/sel.mov"]),
    findSequence: rec("findSequence", seq),
    openSequence: rec("openSequence", true),
  };
  const report = { ok: true, adapter: "uxp", placed: 3, planned: 3, mismatches: [], errors: [], notes: [], totalMs: 10, sequenceGuid: "G1", sequenceName: "S" };
  const adapters = {
    uxp: { place: rec("uxp.place", report), applyOverride: rec("uxp.applyOverride", { ok: true }) },
    xml: { place: rec("xml.place", Object.assign({}, report, { adapter: "xml", ok: false, needsManualImport: true, xmlPath: "C:/d.xml" })),
      verifyImported: rec("xml.verifyImported", Object.assign({}, report, { adapter: "xml" })) },
  };
  const selftest = { run: rec("selftest.run", { count: 24, spikes: { a: { ok: true }, b: { ok: true, placed: 1, planned: 1, mismatches: 0, totalMs: 1 }, c: { ok: true, placed: 1, planned: 1, mismatches: 0, totalMs: 1 } }, engine: { gateFailures: [], withinOneFramePct: 100 }, defaultAdapter: "uxp" }), last: () => null };
  const store = new Map();
  const storage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
  const lfs = { getFileForOpening: rec("pick", (o) => (o.allowMultiple ? [{ nativePath: "D:/shoot/takes/a.mov" }, { nativePath: "D:/shoot/takes/b.mov" }] : { nativePath: "D:/shoot/Song Master.wav" })) };
  const doc = fakeDom();
  const common = createCommon({}, core, {});
  const view = createView({ doc, core, api, pr, adapters, selftest, common, storage, lfs, hostVersion: "26.5.0", setInterval: () => 0 });
  return { view, doc, calls, storage };
}

const names = (calls) => calls.map((c) => c[0]);

test("every button in index.html has a click handler", async () => {
  const { view, doc } = setup();
  view.init();
  await settle();
  const buttons = [...HTML.matchAll(/<button[^>]*\bid="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(buttons.length >= 14);
  for (const id of buttons) assert.ok((doc.els.get(id).listeners.click || []).length === 1, `button ${id} has no handler`);
});

test("each button performs its real operation", async () => {
  const { view, doc, calls, storage } = setup();
  view.init();
  await settle();
  const click = async (id) => { await doc.els.get(id).fire("click"); await settle(); };
  await click("pickMaster");
  assert.strictEqual(view.state.masterPath, "D:/shoot/Song Master.wav");
  await click("analyseMaster");
  assert.ok(names(calls).includes("master"));
  await click("pickFiles");
  await click("useSelection");
  assert.ok(names(calls).includes("selectedMediaPaths"));
  assert.strictEqual(view.state.clipPaths.length, 3);
  await click("analyseClips");
  assert.ok(names(calls).includes("createJob"));
  for (let i = 0; i < 5 && !view.state.result; i++) await new Promise((r) => setTimeout(r, 700));
  assert.ok(view.state.result, "result loaded after the job finished");
  await click("generate");
  assert.match(doc.els.get("banner").innerHTML, /No placement method/, "auto with no self-test asks instead of guessing");
  doc.els.get("adapter").value = "uxp";
  await click("generate");
  assert.ok(names(calls).includes("uxp.place") && names(calls).includes("saveReport"));
  assert.match(doc.els.get("summary").innerHTML, /verified/);
  await click("openSequence");
  assert.ok(names(calls).includes("openSequence"));
  doc.els.get("adapter").value = "xml";
  await click("generate");
  assert.match(doc.els.get("summary").innerHTML, /File &gt; Import/);
  await click("verifyImported");
  assert.ok(names(calls).includes("xml.verifyImported"));
  await click("selftestQuick");
  await click("selftestFull");
  assert.deepStrictEqual(calls.filter((c) => c[0] === "selftest.run").map((c) => c[1].count), [24, 200]);
  view.state.job = { state: "running", progress: {} };
  await click("cancelJob");
  assert.ok(names(calls).includes("cancel"));
  await click("resumeJob");
  assert.ok(names(calls).includes("resume"));
  doc.els.get("tokenInput").value = "abc";
  await click("saveToken");
  assert.ok(calls.some((c) => c[0] === "setToken" && c[1] === "abc"));
  await click("clearFootage");
  assert.strictEqual(view.state.clipPaths.length, 0);
  assert.ok(storage.getItem("autosync.state"));
});

test("review buttons save overrides through the helper and move the draft clip", async () => {
  const { view, doc, calls } = setup();
  view.init();
  await settle();
  view.state.jobId = "J1";
  view.state.result = RESULT;
  view.state.draft = { sequenceGuid: "G1", sequenceName: "S" };
  const placed = RESULT.clips.find((c) => c.status === "HIGH");
  const list = doc.els.get("reviewList");
  const btn = (act, id, extra) => ({ tagName: "BUTTON", parentNode: list, getAttribute: (k) => ({ "data-act": act, "data-id": id, ...(extra || {}) }[k]) });
  await list.fire("click", { target: btn("nudge", placed.clip_id, { "data-d": "1" }) });
  await settle();
  const ov = calls.find((c) => c[0] === "override");
  const tpf = core.ticksPerFrame(RESULT.sequence.fps_num, RESULT.sequence.fps_den);
  assert.ok(Math.abs(ov[3] - core.ticksToSeconds(BigInt(placed.song_position_ticks) + tpf)) < 1e-9);
  assert.ok(names(calls).includes("uxp.applyOverride"));
  doc.getElementById(`pos-${placed.clip_id}`).value = "00:00:10:00";
  await list.fire("click", { target: btn("set", placed.clip_id) });
  await list.fire("click", { target: btn("alt", placed.clip_id, { "data-s": "12.5" }) });
  await list.fire("click", { target: btn("clear", placed.clip_id) });
  await list.fire("click", { target: btn("preview", placed.clip_id) });
  await settle();
  const ovs = calls.filter((c) => c[0] === "override").map((c) => c[3]);
  assert.strictEqual(ovs.length, 4);
  assert.strictEqual(ovs[3], null, "clear sends null");
  assert.ok(names(calls).includes("openSequence"), "preview jumps the playhead");
});
