"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const path = require("path");

const core = require("../js/core.js");
const { createApi, HelperError } = require("../js/api.js");
const { createPremiere } = require("../js/premiere.js");
const { createCommon } = require("../js/placement/common.js");
const { createUxpAdapter } = require("../js/placement/uxp.js");
const { createXmlAdapter } = require("../js/placement/xml.js");
const { createFake } = require("./fake-ppro.js");

const FIX = path.join(__dirname, "fixtures");
const RESULT = JSON.parse(fs.readFileSync(path.join(FIX, "sync_result.json"), "utf8"));
const XML_NAME = "AutoSync - Song Master - fixture";

function setup(knobs) {
  const mediaInfo = new Map();
  mediaInfo.set(RESULT.master.path, { durationTicks: core.secondsToTicks(RESULT.master.duration_s), hasVideo: false, hasAudio: true });
  const unimportable = new Set();
  for (const c of RESULT.clips) {
    mediaInfo.set(c.path, { durationTicks: core.secondsToTicks(c.duration_s || 1), hasVideo: Boolean(c.source_fps), hasAudio: c.has_audio });
    if (c.reason === "DECODE_ERROR") unimportable.add(c.path);
  }
  const fake = createFake(Object.assign({ mediaInfo, unimportable }, knobs || {}));
  const pr = createPremiere(fake.ppro, core);
  const common = createCommon(fake.ppro, core, pr);
  const api = { exportXml: async () => ({ path: path.join(FIX, "draft.xml") }) };
  return { fake, pr, common, uxp: createUxpAdapter(fake.ppro, core, pr, common), xml: createXmlAdapter(fake.ppro, core, pr, common, api) };
}

const plan = (opts) => core.buildPlan(RESULT, Object.assign({ sequenceName: XML_NAME }, opts || {}));
const counts = core.counts(RESULT);

test("ticks per frame are exact integers for common rates", () => {
  assert.strictEqual(core.ticksPerFrame(24000, 1001), 10594584000n);
  assert.strictEqual(core.ticksPerFrame(30000, 1001), 8475667200n);
  assert.strictEqual(core.ticksPerFrame(25, 1), 10160640000n);
  assert.throws(() => core.ticksPerFrame(11, 1)); // 254016000000 / 11 is not whole
  assert.strictEqual(core.timecode(core.ticksPerFrame(25, 1) * 25n * 61n + core.ticksPerFrame(25, 1) * 3n, 25, 1), "00:01:01:03");
});

test("plan places HIGH/MEDIUM, bins UNSYNCED, keeps LOW for review", () => {
  const p = plan();
  assert.strictEqual(p.items.length, counts.HIGH + counts.MEDIUM);
  assert.strictEqual(p.unsynced.length, counts.UNSYNCED);
  assert.strictEqual(p.review.length, counts.LOW);
  assert.strictEqual(plan({ placeLow: true }).items.length, counts.HIGH + counts.MEDIUM + counts.LOW);
  for (const it of p.items) {
    assert.strictEqual(it.startTicks % p.ticksPerFrame, 0n);
    assert.strictEqual(it.aIdx, it.vIdx + 1);
    assert.ok(p.farTicks > it.startTicks + it.durationTicks);
  }
  assert.strictEqual(p.errors.length, 0);
});

test("UXP adapter places every clip and readback matches", async () => {
  const s = setup();
  const user = await s.fake.project.createSequence("Editor's own cut");
  const before = user.snapshot();
  const p = plan();
  const r = await s.uxp.place(p, { batchSize: 4 });
  assert.deepStrictEqual(r.mismatches, []);
  assert.deepStrictEqual(r.errors, []);
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.placed, p.items.length);
  assert.ok(r.timings.stagedTracks > 0, "tracks beyond the default preset were created by staging");
  assert.strictEqual(user.snapshot(), before, "existing sequence untouched");
  const seq = s.fake.project.sequences.find((q) => q.name === XML_NAME);
  assert.strictEqual(seq.tpf, 10594584000n, "sequence frame rate set to 23.976");
  for (let i = 1; i <= p.scratchTracks; i++) assert.strictEqual(seq.audio[i].muted, true, `A${i + 1} muted`);
  assert.strictEqual(seq.audio[0].muted, false, "master track A1 not muted");
  assert.ok(s.fake.project.opened.includes(seq.guid) && s.fake.project.active === seq);
  // pre-roll clip: in-point set so the song lines up
  const pre = RESULT.clips.find((c) => Number(c.source_in_ticks) > 0 && core.placeable(c));
  const item = seq.video.flatMap((t) => t.items).find((i) => i.clip.path === pre.path);
  assert.strictEqual(String(item.inTicks), pre.source_in_ticks);
  // source marks restored on the project items
  const clip = s.fake.project._findClip(pre.path);
  assert.strictEqual(clip.inTicks, 0n);
  // bins
  const autosync = s.fake.project.root.children.find((c) => c.name === "AutoSync");
  const unsynced = autosync.children.find((c) => c.name === "Unsynced");
  assert.strictEqual(unsynced.children.length + r.binned.notImported.length, counts.UNSYNCED);
  assert.ok(r.binned.notImported.length >= 1, "corrupt file listed, not hidden");
});

test("UXP adapter never relies on undocumented overwrite track creation", async () => {
  // Whether overwrite creates tracks is not documented; the adapter stages new tracks with
  // insert either way, so the result is the same on both behaviours.
  for (const overwriteCreatesTracks of [true, false]) {
    const s = setup({ overwriteCreatesTracks });
    const r = await s.uxp.place(plan(), {});
    assert.strictEqual(r.ok, true, JSON.stringify(r.mismatches));
    assert.strictEqual(r.timings.stagedTracks, 3);
  }
});

test("a misplaced clip is reported, never hidden", async () => {
  const s = setup({ shiftClipIndex: 6, shiftOneClipBy: core.ticksPerFrame(24000, 1001) });
  const r = await s.uxp.place(plan(), {});
  assert.strictEqual(r.ok, false);
  const m = r.mismatches.filter((x) => x.kind === "video_start");
  assert.strictEqual(m.length, 1);
  assert.strictEqual(m[0].frames, 1);
});

test("XML adapter: Python-written XML reads back exactly as the JS plan", async () => {
  const s = setup({ xmlImport: "creates" });
  const r = await s.xml.place(plan(), { jobId: "job" });
  assert.deepStrictEqual(r.mismatches, []);
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.placed, plan().items.length);
});

test("XML adapter: no sequence from importFiles -> manual import path, then verify", async () => {
  const s = setup({ xmlImport: "none" });
  const r = await s.xml.place(plan(), { jobId: "job" });
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.needsManualImport, true);
  // the user imports by hand and opens the sequence
  s.fake.knobs.xmlImport = "creates";
  await s.fake.project.importFiles([path.join(FIX, "draft.xml")], true, null, false);
  s.fake.project.active = s.fake.project.sequences.find((q) => q.name === XML_NAME);
  const v = await s.xml.verifyImported(plan());
  assert.strictEqual(v.ok, true, JSON.stringify(v.mismatches));
});

test("manual correction moves only that clip; an unsynced clip with an override gets placed", async () => {
  const s = setup();
  const p = plan();
  const r = await s.uxp.place(p, {});
  assert.ok(r.ok);
  const draft = { sequenceGuid: r.sequenceGuid, sequenceName: p.sequenceName };
  const seq = s.fake.project.sequences.find((q) => q.guid === r.sequenceGuid);
  const positions = () => new Map([...seq.video, ...seq.audio].flatMap((t, ti) => t.items.map((i) => [`${ti}:${i.clip.path}`, i.start])));
  const before = positions();
  const target = p.items[0];
  const moved = Object.assign({}, target, { startTicks: target.startTicks + p.ticksPerFrame });
  const res = await s.uxp.applyOverride(draft, p, moved);
  assert.strictEqual(res.ok, true);
  const after = positions();
  for (const [k, v] of before) {
    if (k.endsWith(target.path)) assert.strictEqual(after.get(k), v + p.ticksPerFrame, `${k} moved one frame (video and scratch audio)`);
    else assert.strictEqual(after.get(k), v, `${k} untouched`);
  }
  // place an UNSYNCED clip via override (has audio, no position from the engine)
  const un = RESULT.clips.find((c) => c.status === "UNSYNCED" && c.has_audio && c.source_fps && c.reason === "NO_MATCH");
  const withOverride = JSON.parse(JSON.stringify(RESULT));
  const cc = withOverride.clips.find((c) => c.clip_id === un.clip_id);
  cc.override = { song_position_s: 1.0, song_position_ticks: String(core.ticksPerFrame(24000, 1001) * 24n), by: "t", at: "x", video_track: p.videoTracks + 1 };
  const p2 = core.buildPlan(withOverride, { sequenceName: p.sequenceName });
  const it2 = p2.items.find((i) => i.clipId === un.clip_id);
  const res2 = await s.uxp.applyOverride(draft, p2, it2);
  assert.strictEqual(res2.ok, true);
});

test("readback flags unmuted scratch tracks and unexpected items", () => {
  const p = plan();
  const ids = new Map(p.items.map((it, i) => [it.clipId, `id${i}`]));
  const video = p.items.map((it, i) => ({ trackIndex: it.vIdx, startTicks: String(it.startTicks), inTicks: String(it.inTicks), projectItemId: `id${i}`, name: it.name }));
  const audio = [{ trackIndex: 0, startTicks: "0", projectItemId: "m", name: "master" }]
    .concat(p.items.filter((it) => it.hasAudio).map((it) => ({ trackIndex: it.aIdx, startTicks: String(it.startTicks), projectItemId: ids.get(it.clipId), name: it.name })));
  const muted = [false].concat(Array(p.scratchTracks).fill(true));
  assert.strictEqual(core.compareReadback(p, { video, audio, mutedAudio: muted }, ids, "m").ok, true);
  muted[1] = false;
  assert.ok(core.compareReadback(p, { video, audio, mutedAudio: muted }, ids, "m").mismatches.some((m) => m.kind === "scratch_track_not_muted"));
  muted[1] = true;
  video.push({ trackIndex: 0, startTicks: "0", projectItemId: "stranger", name: "x" });
  assert.ok(core.compareReadback(p, { video, audio, mutedAudio: muted }, ids, "m").mismatches.some((m) => m.kind === "unexpected_item"));
});

test("default adapter: only clean self-test reports qualify, fastest wins", () => {
  assert.strictEqual(core.pickDefaultAdapter([{ adapter: "uxp", ok: true, placed: 5, totalMs: 900 }, { adapter: "xml", ok: true, placed: 5, totalMs: 300 }]), "xml");
  assert.strictEqual(core.pickDefaultAdapter([{ adapter: "uxp", ok: true, placed: 5, totalMs: 900 }, { adapter: "xml", ok: false, placed: 5, totalMs: 1 }]), "uxp");
  assert.strictEqual(core.pickDefaultAdapter([{ adapter: "uxp", ok: false }]), null);
});

test("helper client: token from file, JSON errors, helper down", async () => {
  const files = { "C:/Users/ed/AppData/Roaming/MusicVideoAutoSync/token": "tok123\n" };
  let lastHeaders = null;
  const fetchOk = async (url, init) => { lastHeaders = init.headers; return { ok: true, status: 200, text: async () => '{"status":"ok"}' }; };
  const api = createApi({ fetch: fetchOk, fs: { readFile: async (p) => { if (!(p in files)) throw new Error("ENOENT"); return files[p]; } }, os: { homedir: () => "C:\\Users\\ed" } });
  assert.deepStrictEqual(await api.health(), { status: "ok" });
  assert.strictEqual(lastHeaders["X-AutoSync-Token"], "tok123");
  const apiErr = createApi({ token: "t", fetch: async () => ({ ok: false, status: 404, text: async () => '{"error":{"code":"JOB_NOT_FOUND","message":"No job x"}}' }) });
  await assert.rejects(apiErr.job("x"), (e) => e instanceof HelperError && e.code === "JOB_NOT_FOUND" && e.status === 404);
  const apiDown = createApi({ token: "t", fetch: async () => { throw new Error("ECONNREFUSED"); } });
  await assert.rejects(apiDown.health(), (e) => e.code === "HELPER_DOWN");
  const apiNoTok = createApi({ fetch: fetchOk, fs: { readFile: async () => { throw new Error("ENOENT"); } }, os: { homedir: () => "C:/x" } });
  await assert.rejects(apiNoTok.health(), (e) => e.code === "TOKEN_NOT_FOUND");
});

test("typed positions: timecode, mm:ss and seconds", () => {
  assert.strictEqual(core.parsePosition("00:01:02:12", 25, 1), 62.48);
  assert.strictEqual(core.parsePosition("1:02.5", 25, 1), 62.5);
  assert.strictEqual(core.parsePosition("62.5", 25, 1), 62.5);
  assert.strictEqual(core.parsePosition("00:00:00:30", 25, 1), null);
  assert.strictEqual(core.parsePosition("abc", 25, 1), null);
});

test("keep scratch audio off: UXP keeps it muted and says so, readback still clean", async () => {
  const s = setup();
  const p = plan({ keepScratch: false });
  assert.strictEqual(p.scratchTracks, 0);
  const r = await s.uxp.place(p, {});
  assert.strictEqual(r.ok, true, JSON.stringify(r.mismatches));
  assert.ok(r.notes.some((n) => /scratch audio was kept and muted/.test(n)));
  assert.ok(r.mutedTracks.length > 0);
});

test("helper client finds the token where the helper writes it on macOS", async () => {
  const files = { "/Users/ed/Library/Application Support/MusicVideoAutoSync/token": "mactok\n" };
  let sent = null;
  const api = createApi({ fetch: async (u, init) => { sent = init.headers; return { ok: true, status: 200, text: async () => "{}" }; },
    fs: { readFile: async (p) => { if (!(p in files)) throw new Error("ENOENT"); return files[p]; } }, os: { homedir: () => "/Users/ed" } });
  await api.health();
  assert.strictEqual(sent["X-AutoSync-Token"], "mactok");
});

test("helper client falls back to https when http is refused (macOS) and remembers it", async () => {
  const seen = [];
  const fetchFn = async (url) => {
    seen.push(url);
    if (url.startsWith("http://")) throw new TypeError("Network request failed");
    return { ok: true, status: 200, text: async () => '{"status":"ok"}' };
  };
  const api = createApi({ token: "t", fetch: fetchFn });
  assert.deepStrictEqual(await api.health(), { status: "ok" });
  assert.strictEqual(api.base, "https://127.0.0.1:47653");
  seen.length = 0;
  await api.health();
  assert.deepStrictEqual(seen, ["https://127.0.0.1:47653/health"], "the working base is used first next time");
  const down = createApi({ token: "t", fetch: async () => { throw new TypeError("refused"); } });
  await assert.rejects(down.health(), (e) => e.code === "HELPER_DOWN" && /https:\/\/localhost/.test(e.message));
});
