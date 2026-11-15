"use strict";
/*
 * Panel UI. Every control triggers a real operation (spec rule 2); errors are shown, never
 * swallowed; a draft is reported as successful only when its readback matched (spec section 7).
 * File picker: require("uxp").storage.localFileSystem.getFileForOpening({allowMultiple, types}),
 * entry.nativePath -- VERIFIED, docs/PREMIERE_API_NOTES.md item 10.
 */

const MEDIA_TYPES = ["mov", "mp4", "m4v", "mxf", "avi", "mkv", "mts", "m2ts", "mpg", "mpeg", "wmv", "3gp",
  "wav", "mp3", "aac", "m4a", "aif", "aiff", "flac", "ogg"];
const AUDIO_TYPES = ["wav", "mp3", "aif", "aiff", "flac", "m4a", "aac", "ogg"];

function createView(deps) {
  const { doc, core, api, pr, adapters, selftest, common, storage, lfs, hostVersion, firstcut } = deps;
  const $ = (id) => doc.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const saved = (() => { try { return JSON.parse(storage.getItem("autosync.state") || "{}"); } catch (e) { return {}; } })();
  const state = {
    helper: "unknown", health: null, busy: false,
    masterPath: saved.masterPath || null, master: null,
    clipPaths: saved.clipPaths || [], probe: null,
    jobId: saved.jobId || null, job: null, result: null,
    draft: saved.draft || null, lastReport: null,
    cutSeed: saved.cutSeed || 0, cut: saved.cut || null, shots: null,
  };

  function persist() {
    try {
      storage.setItem("autosync.state", JSON.stringify({ masterPath: state.masterPath, clipPaths: state.clipPaths, jobId: state.jobId,
        draft: state.draft, settings: settingsSnapshot(), cutSeed: state.cutSeed, cut: state.cut }));
    } catch (e) { /* non-critical */ }
  }

  function settingsSnapshot() {
    return { keepScratch: $("keepScratch").checked, muteScratch: $("muteScratch").checked, placeLow: $("placeLow").checked,
      rate2: $("rate2").checked, fps: $("fps").value, res: $("res").value, adapter: $("adapter").value,
      cutStyle: $("cutStyle").value, cutPace: $("cutPace").value, cutEffects: $("cutEffects").checked,
      cutColour: $("cutColour").checked, cutLook: $("cutLook").value };
  }

  function restoreSettings() {
    const s = saved.settings || {};
    for (const k of ["keepScratch", "muteScratch", "placeLow", "rate2", "cutEffects", "cutColour"]) if (k in s) $(k).checked = Boolean(s[k]);
    for (const k of ["fps", "res", "adapter", "cutStyle", "cutPace", "cutLook"]) if (s[k]) $(k).value = s[k];
    if (!s.cutLook) $("cutLook").value = "none";
    if (!s.cutPace) $("cutPace").value = "1"; // "Normal", whatever the host does with `selected`
  }

  function sequenceSettings() {
    const [num, den] = $("fps").value.split("/").map(Number);
    const [w, h] = $("res").value.split("x").map(Number);
    return { fps_num: num, fps_den: den, width: w, height: h };
  }

  function banner(kind, html) {
    const b = $("banner");
    if (!kind) { b.className = "banner hidden"; b.innerHTML = ""; return; }
    b.className = `banner ${kind}`;
    b.innerHTML = html;
  }

  function errText(e) {
    if (!e) return "Unknown error";
    return e.message || String(e);
  }

  async function guarded(label, fn) {
    if (state.busy) return;
    state.busy = true;
    render();
    try {
      await fn();
    } catch (e) {
      banner("error", `<b>${esc(label)} failed.</b> ${esc(errText(e))}`);
      if (e && (e.code === "HELPER_DOWN" || e.code === "BAD_TOKEN" || e.code === "TOKEN_NOT_FOUND")) await checkHelper();
    } finally {
      state.busy = false;
      render();
    }
  }

  // ------------------------------------------------------------------ helper status
  async function checkHelper() {
    try {
      const h = await api.health();
      state.health = h;
      state.helper = h.ffmpeg_found ? "ok" : "noffmpeg";
      $("tokenBox").className = "card hidden";
    } catch (e) {
      state.health = null;
      if (e.code === "TOKEN_NOT_FOUND" || e.code === "BAD_TOKEN") {
        state.helper = "token";
        $("tokenBox").className = "card";
      } else {
        state.helper = "down";
      }
    }
    renderHelper();
  }

  function renderHelper() {
    const dot = $("helperDot");
    const txt = $("helperText");
    const map = {
      ok: ["green", "Helper connected"],
      noffmpeg: ["amber", "Helper running, FFmpeg missing"],
      token: ["amber", "Helper needs pairing"],
      down: ["red", "Helper not running"],
      unknown: ["grey", "Checking helper…"],
    };
    const [c, t] = map[state.helper] || map.unknown;
    dot.className = `dot ${c}`;
    txt.textContent = t;
    if (state.helper === "down") {
      banner("error", "<b>Helper not running.</b> Windows: start <b>Music Video AutoSync Helper</b> from the Start menu (it normally starts with Windows). Mac: in Terminal, in the cloned folder, run <b>./scripts/start_helper_mac.sh</b> and keep that window open. Analysis and placement need the helper; nothing is faked while it is down.");
    } else if (state.helper === "noffmpeg") {
      banner("warn", "<b>FFmpeg not found by the helper.</b> Windows: reinstall Music Video AutoSync (the installer ships FFmpeg). Mac: run <b>brew install ffmpeg</b>, then restart the helper.");
    } else if (state.helper === "ok" && /Helper not running|FFmpeg not found/.test($("banner").innerHTML)) {
      banner(null);
    }
    const h = state.health;
    $("about").innerHTML = `Panel 0.3.0 · Premiere ${esc(hostVersion || "?")}` +
      (h ? ` · Helper ${esc(h.version)} · Engine ${esc(h.engine_version)}<br>Cache: ${esc(h.cache_dir)}` : "");
  }

  // ------------------------------------------------------------------ master
  async function pickMaster() {
    const f = await lfs.getFileForOpening({ allowMultiple: false, types: AUDIO_TYPES });
    const file = Array.isArray(f) ? f[0] : f;
    if (!file) return;
    state.masterPath = file.nativePath;
    state.master = null;
    persist();
    render();
  }

  async function analyseMaster() {
    $("masterInfo").textContent = "Analysing…";
    state.master = await api.master(state.masterPath);
    persist();
    render();
  }

  function renderWaveform() {
    const w = $("waveform");
    if (!state.master) { w.innerHTML = ""; return; }
    w.innerHTML = state.master.peaks.map((p) => `<div class="wbar" style="height:${Math.max(1, Math.round(p * 40))}px"></div>`).join("");
  }

  // ------------------------------------------------------------------ footage
  async function pickFiles() {
    const files = await lfs.getFileForOpening({ allowMultiple: true, types: MEDIA_TYPES });
    const list = (Array.isArray(files) ? files : files ? [files] : []).map((f) => f.nativePath);
    if (!list.length) return;
    await setFootage(list);
  }

  async function useSelection() {
    const project = await pr.activeProject();
    const { paths, selected } = await pr.selectedMediaPaths(project);
    if (!paths.length) {
      if (!selected) throw new Error("Premiere reports nothing selected in the Project panel. Click your clips there (or the bin that holds them), then click this button again. Or use Import files… to pick the files.");
      throw new Error(`Premiere reports ${selected} selected item(s) in the Project panel, but none is a media file. Select video clips or the bin that holds them, or use Import files….`);
    }
    await setFootage(paths);
  }

  async function setFootage(paths) {
    const master = core.normPath(state.masterPath);
    const seen = new Set(state.clipPaths.map(core.normPath));
    for (const p of paths) if (!seen.has(core.normPath(p)) && core.normPath(p) !== master) { state.clipPaths.push(p); seen.add(core.normPath(p)); }
    state.probe = await api.probe(state.clipPaths, []);
    persist();
  }

  // ------------------------------------------------------------------ analysis job
  async function analyseClips() {
    if (!state.masterPath) throw new Error("Select the master song first.");
    if (!state.clipPaths.length) throw new Error("Add footage first.");
    const settings = { rates: $("rate2").checked ? [1.0, 2.0] : [1.0] };
    const r = await api.createJob(state.masterPath, state.clipPaths, sequenceSettings(), settings);
    state.jobId = r.job_id;
    state.result = null;
    state.draft = null;
    persist();
    pollJob();
  }

  let polling = false;
  async function pollJob() {
    if (polling || !state.jobId) return;
    polling = true;
    try {
      for (;;) {
        let j;
        try { j = await api.job(state.jobId); } catch (e) {
          if (e.code === "JOB_NOT_FOUND") { state.jobId = null; persist(); }
          throw e;
        }
        state.job = j;
        renderProgress();
        if (["done", "cancelled", "failed", "interrupted"].includes(j.state)) {
          if (j.state !== "failed" && j.state !== "interrupted") {
            try { state.result = await api.result(state.jobId); } catch (e) { state.result = null; }
          }
          if (j.state === "failed") banner("error", `<b>Analysis failed.</b> ${esc(j.error ? j.error.message : "")}`);
          if (j.state === "interrupted") banner("warn", "<b>The helper stopped during analysis.</b> Click Resume to continue; finished clips are kept.");
          break;
        }
        await sleep(600);
      }
    } catch (e) {
      banner("error", `<b>Lost contact with the helper.</b> ${esc(errText(e))}`);
    } finally {
      polling = false;
      render();
    }
  }

  function renderProgress() {
    const j = state.job;
    const p = j ? j.progress : { done: 0, total: 0, HIGH: 0, MEDIUM: 0, LOW: 0, UNSYNCED: 0 };
    $("stDone").textContent = `${p.done || 0}/${p.total || 0}`;
    $("stSynced").textContent = String((p.HIGH || 0) + (p.MEDIUM || 0));
    $("stUnsynced").textContent = String(p.UNSYNCED || 0);
    $("stLow").textContent = String(p.LOW || 0);
    const pct = p.total ? Math.round((100 * (p.done || 0)) / p.total) : 0;
    $("barFill").style.width = `${pct}%`;
    $("progressText").textContent = j ? `${j.state}${p.phase && p.phase !== j.state ? " · " + p.phase : ""}${p.current ? " · " + p.current : ""}` : "Idle";
    const running = j && (j.state === "running" || j.state === "queued");
    $("cancelJob").disabled = !running;
    $("resumeJob").className = j && ["interrupted", "cancelled"].includes(j.state) ? "" : "hidden";
  }

  // ------------------------------------------------------------------ generate
  function chosenAdapter() {
    const v = $("adapter").value;
    if (v !== "auto") return v;
    return storage.getItem("autosync.defaultAdapter") || null;
  }

  function buildPlan() {
    return core.buildPlan(state.result, {
      sequenceName: common.sequenceName(state.result.master.path, new Date()),
      placeLow: $("placeLow").checked, keepScratch: $("keepScratch").checked, muteScratch: $("muteScratch").checked,
    });
  }

  async function generate() {
    if (!state.result) throw new Error("Analyse the clips first.");
    const which = chosenAdapter();
    if (!which) throw new Error("No placement method chosen yet. Run the placement self-test below, or pick UXP direct or XML import in Sync settings.");
    const plan = buildPlan();
    if (!plan.items.length) throw new Error("No clip is ready to place (none rated HIGH or MEDIUM, no overrides). See the Review list.");
    $("summary").className = "summary";
    $("summary").textContent = `Placing ${plan.items.length} clips with ${which === "uxp" ? "UXP direct" : "XML import"}…`;
    const report = await adapters[which].place(plan, {
      jobId: state.jobId,
      onProgress: (p) => { $("summary").textContent = `${p.phase} ${p.done || 0}/${p.total || 0}`; },
    });
    state.lastReport = report;
    state.lastPlan = plan;
    if (report.sequenceGuid) {
      state.draft = { sequenceGuid: report.sequenceGuid, sequenceName: plan.sequenceName, adapter: which, at: new Date().toISOString() };
    }
    persist();
    try { await api.saveReport(state.jobId, "placement_report", stripReport(report)); } catch (e) { report.notes = (report.notes || []).concat(`Report not saved: ${e.message}`); }
    renderSummary(report, plan);
  }

  function stripReport(r) {
    const out = Object.assign({}, r);
    delete out.idByClip;
    return JSON.parse(JSON.stringify(out, (k, v) => (typeof v === "bigint" ? String(v) : v)));
  }

  function renderSummary(r, plan) {
    const s = $("summary");
    const ok = r.ok;
    s.className = `summary ${ok ? "ok" : "bad"}`;
    const lines = [];
    if (r.needsManualImport) {
      lines.push(`<b>XML written but Premiere did not import it automatically.</b> In Premiere choose File &gt; Import, select:<br><span class="pos">${esc(r.xmlPath)}</span><br>open the new sequence <b>${esc(plan.sequenceName)}</b>, then click <b>Verify imported sequence</b>.`);
    } else if (ok) {
      lines.push(`<span class="pass">Draft ready and verified.</span> ${r.placed}/${r.planned} clips placed; every position read back from Premiere matched.`);
    } else {
      lines.push(`<span class="fail">Draft finished with problems.</span> ${r.placed}/${r.planned} placed, ${r.mismatches.length} readback mismatch(es), ${r.errors.length} error(s). Nothing below is hidden.`);
    }
    lines.push(`Sequence: <b>${esc(r.sequenceName)}</b> · method ${r.adapter === "uxp" ? "UXP direct" : "XML import"} · ${(r.totalMs / 1000).toFixed(1)} s`);
    if (r.binned) lines.push(`Bins: ${r.binned.unsynced} unsynced clip(s) in AutoSync / Unsynced, ${r.binned.review} in AutoSync / Needs review${r.binned.notImported.length ? `; Premiere could not import: ${esc(r.binned.notImported.join(", "))}` : ""}`);
    if (r.mutedTracks && r.mutedTracks.length) lines.push(`Scratch audio muted on A${r.mutedTracks.join(", A")}`);
    for (const m of r.mismatches.slice(0, 12)) lines.push(`<span class="fail">Mismatch</span> ${esc(m.name)}: ${esc(m.kind)}${m.expected ? ` expected ${esc(m.expected)}` : ""}${m.actual ? ` got ${esc(m.actual)}` : ""}${m.frames ? ` (${m.frames} frames)` : ""}`);
    if (r.mismatches.length > 12) lines.push(`…and ${r.mismatches.length - 12} more (saved in the helper's placement_report.json)`);
    for (const e of r.errors.slice(0, 12)) lines.push(`<span class="fail">Error</span> ${esc(e.name || "")} ${esc(e.error)}`);
    for (const n of r.notes || []) lines.push(`<span class="muted">${esc(n)}</span>`);
    s.innerHTML = lines.join("<br>");
    $("verifyImported").className = r.needsManualImport ? "" : "hidden";
    state.pendingPlan = r.needsManualImport ? plan : null;
  }

  async function verifyImported() {
    if (!state.pendingPlan) throw new Error("Nothing to verify.");
    const r = await adapters.xml.verifyImported(state.pendingPlan);
    state.lastReport = r;
    if (r.sequenceGuid) state.draft = { sequenceGuid: r.sequenceGuid, sequenceName: state.pendingPlan.sequenceName, adapter: "xml", at: new Date().toISOString() };
    persist();
    try { await api.saveReport(state.jobId, "placement_report", stripReport(r)); } catch (e) { /* shown below */ }
    renderSummary(r, state.pendingPlan);
  }

  async function openSequence() {
    if (!state.draft) throw new Error("No draft yet.");
    const project = await pr.activeProject();
    const seq = await pr.findSequence(project, state.draft.sequenceGuid);
    if (!seq) throw new Error("The draft sequence is no longer in this project.");
    await pr.openSequence(project, seq, 0n);
  }

  // ------------------------------------------------------------------ review list
  function effectiveTicks(c) {
    if (c.override) return BigInt(c.override.song_position_ticks);
    return c.song_position_ticks != null ? BigInt(c.song_position_ticks) : null;
  }

  function renderReview() {
    const list = $("reviewList");
    if (!state.result) { list.innerHTML = ""; $("reviewCount").textContent = ""; return; }
    const { fps_num: fn, fps_den: fd } = state.result.sequence;
    const filter = $("filter").value;
    const rows = state.result.clips.filter((c) => filter === "ALL" || c.status === filter)
      .sort((a, b) => ({ UNSYNCED: 0, LOW: 1, MEDIUM: 2, HIGH: 3 }[a.status] - { UNSYNCED: 0, LOW: 1, MEDIUM: 2, HIGH: 3 }[b.status]) || a.name.localeCompare(b.name));
    $("reviewCount").textContent = `${rows.length} of ${state.result.clips.length} clips`;
    list.innerHTML = rows.map((c) => {
      const t = effectiveTicks(c);
      const pos = t == null ? "—" : core.timecode(t, fn, fd);
      const why = [];
      if (c.reason) why.push(`${c.reason}${c.reason_detail ? ": " + c.reason_detail : ""}`);
      why.push(`score ${c.confidence.score} · ratio ${c.confidence.peak_ratio} · windows ${c.confidence.window_agreement == null ? "n/a" : c.confidence.window_agreement}`);
      if (c.rate && c.rate !== 1) why.push(`playback ${c.rate}x (conform in Premiere: Interpret Footage)`);
      if (c.drift) why.push(c.drift.reliable ? `drift ${c.drift.ppm} ppm` : "drift unreliable");
      if (c.override) why.push(`override by ${c.override.by} at ${c.override.at}`);
      const alts = (c.alternates || []).slice(0, 4).map((a) =>
        `<button class="mini" data-act="alt" data-id="${esc(c.clip_id)}" data-s="${a.song_position_s}">Use ${esc(core.timecode(core.secondsToTicks(Math.max(0, a.song_position_s)), fn, fd))} (${a.score})</button>`).join("");
      return `<div class="clip"><div class="top"><span class="badge ${esc(c.status)}">${esc(c.status)}</span>${c.override ? '<span class="badge OVR">OVR</span>' : ""}` +
        `<span class="name">${esc(c.name)}</span><span class="pos">${esc(pos)}</span></div>` +
        `<div class="why">${esc(why.join(" · "))}</div>` +
        (alts ? `<div class="alts">${alts}</div>` : "") +
        `<div class="row"><button class="mini" data-act="nudge" data-id="${esc(c.clip_id)}" data-d="-1">−1f</button>` +
        `<button class="mini" data-act="nudge" data-id="${esc(c.clip_id)}" data-d="1">+1f</button>` +
        `<input class="posinput" type="text" id="pos-${esc(c.clip_id)}" placeholder="hh:mm:ss:ff">` +
        `<button class="mini" data-act="set" data-id="${esc(c.clip_id)}">Set</button>` +
        (c.override ? `<button class="mini" data-act="clear" data-id="${esc(c.clip_id)}">Clear</button>` : "") +
        `<button class="mini" data-act="preview" data-id="${esc(c.clip_id)}">Preview</button></div></div>`;
    }).join("");
  }

  async function saveOverride(clipId, seconds) {
    if (seconds != null && seconds < 0) throw new Error("Positions before the start of the song are not allowed.");
    await api.override(state.jobId, clipId, seconds, "editor");
    state.result = await api.result(state.jobId);
    const c = state.result.clips.find((x) => x.clip_id === clipId);
    let msg = seconds == null ? `Override cleared for ${c.name}.` : `Saved ${c.name} at ${core.timecode(BigInt(c.override.song_position_ticks), state.result.sequence.fps_num, state.result.sequence.fps_den)} (V${c.override.video_track}).`;
    if (seconds != null && state.draft) {
      const plan = core.buildPlan(state.result, { sequenceName: state.draft.sequenceName, placeLow: true,
        keepScratch: $("keepScratch").checked, muteScratch: $("muteScratch").checked });
      const it = plan.items.find((i) => i.clipId === clipId);
      const r = await adapters.uxp.applyOverride(state.draft, plan, it);
      msg += r.ok ? " Moved in the draft and verified." : ` Draft NOT updated: expected ${r.expected}, Premiere reports ${r.actual}.`;
    } else if (seconds != null) {
      msg += " It will be used by the next GENERATE FIRST DRAFT.";
    } else if (state.draft) {
      msg += " The draft keeps its current position until you generate again.";
    }
    $("reviewMsg").innerHTML = esc(msg);
  }

  async function onReviewClick(ev) {
    // Plain getAttribute and a parent walk: data-attribute helpers are not guaranteed in UXP's DOM.
    let b = ev.target;
    while (b && b.tagName !== "BUTTON" && b !== $("reviewList")) b = b.parentNode;
    if (!b || b.tagName !== "BUTTON" || !b.getAttribute("data-act")) return;
    const act = b.getAttribute("data-act");
    const id = b.getAttribute("data-id");
    const c = state.result.clips.find((x) => x.clip_id === id);
    const { fps_num: fn, fps_den: fd } = state.result.sequence;
    await guarded("Review action", async () => {
      if (act === "alt") return saveOverride(id, Math.max(0, Number(b.getAttribute("data-s"))));
      if (act === "clear") return saveOverride(id, null);
      if (act === "nudge") {
        const t = effectiveTicks(c);
        if (t == null) throw new Error("This clip has no position to nudge; type one instead.");
        const ticks = t + BigInt(b.getAttribute("data-d")) * core.ticksPerFrame(fn, fd);
        return saveOverride(id, core.ticksToSeconds(ticks));
      }
      if (act === "set") {
        const s = core.parsePosition($(`pos-${id}`).value, fn, fd);
        if (s == null) throw new Error("Type a position as hh:mm:ss:ff, m:ss.sss or seconds.");
        return saveOverride(id, s);
      }
      if (act === "preview") {
        if (!state.draft) throw new Error("Generate a draft first; preview jumps the playhead in it.");
        const t = effectiveTicks(c);
        if (t == null) throw new Error("This clip has no position.");
        const project = await pr.activeProject();
        const seq = await pr.findSequence(project, state.draft.sequenceGuid);
        if (!seq) throw new Error("The draft sequence is no longer in this project.");
        await pr.openSequence(project, seq, t);
        $("reviewMsg").textContent = `Playhead at ${core.timecode(t, fn, fd)} (${c.name}).`;
      }
    });
  }

  // ------------------------------------------------------------------ first cut
  function cutOptions() {
    return { style: $("cutStyle").value, pace: Number($("cutPace").value), effects: $("cutEffects").checked,
      seed: state.cutSeed, place_low: $("placeLow").checked, colour: $("cutColour").checked, look: $("cutLook").value };
  }

  function renderShots(st) {
    const el = $("shotsInfo");
    if (!st || !st.takes) { el.textContent = "Not analysed yet: the cut balances the takes without looking at the picture."; return; }
    const n = (size) => st.takes.filter((t) => t.size === size).length;
    const setups = new Set(st.takes.map((t) => t.setup).filter(Boolean)).size;
    const faces = st.takes.filter((t) => t.faces > 0.2).length;
    const noDet = st.takes.some((t) => t.face_detector === "none");
    const bad = st.takes.filter((t) => t.error);
    el.textContent = `${st.takes.length} clips analysed: ${n("close")} close, ${n("medium")} medium, ${n("wide")} wide · ${setups} camera setups · faces in ${faces}`
      + (noDet ? " · face detection unavailable (OpenCV missing): framing from the picture only" : "")
      + (bad.length ? ` · could not read ${bad.map((t) => t.name).join(", ")}` : "");
  }

  async function analyseShots() {
    if (!state.result) throw new Error("Analyse the clips first.");
    $("shotsInfo").textContent = "Analysing shots…";
    state.shots = await firstcut.analyseShots(state.jobId, (st) => { $("shotsInfo").textContent = `Analysing shots ${st.done}/${st.total}${st.current ? ` · ${st.current}` : ""}`; });
    renderShots(state.shots);
  }

  function renderCut(out) {
    const el = $("cutOut");
    if (!out) { el.className = "summary hidden"; el.innerHTML = ""; return; }
    const st = out.stats || {};
    const lines = [];
    if (out.xml_path && out.imported) {
      lines.push(`<span class="pass">First cut ready.</span> Sequence <b>${esc(out.sequence_name)}</b> is open in the timeline.`);
    } else if (out.xml_path) {
      lines.push(`<b>Cut written, but Premiere did not import it automatically.</b> In Premiere choose File &gt; Import and select:<br><span class="pos">${esc(out.xml_path)}</span>`);
    }
    if (st.shots != null) lines.push(`${st.shots} shots (${st.performance_shots} performance, ${st.story_shots} story) · mean shot ${st.mean_shot_s} s · ${st.takes_used} takes · tempo ${Number(out.tempo_bpm).toFixed(1)} BPM · variation ${state.cutSeed + 1}`);
    if (out.sections && out.sections.length) lines.push(`Sections (markers on the sequence): ${out.sections.map((s) => esc(s.kind)).join(" · ")}`);
    if (out.colour) {
      const c = out.colour;
      lines.push(`Colour: every take matched to <b>${esc(c.hero_name)}</b>${c.look !== "none" ? `, look ${esc(c.look)}` : ""}. LUTs: <span class="pos">${esc(c.folder)}</span>`);
      lines.push(`<span class="muted">Premiere cannot be given a LUT by a panel: in Lumetri Color &gt; Basic Correction &gt; Input LUT &gt; Browse, choose each take's <i>_match${c.look !== "none" ? "_" + esc(c.look) : ""}.cube</i> (the clip comment names it). The preview video already has it.</span>`);
    } else if (out.look_lut) {
      lines.push(`Look LUT: <span class="pos">${esc(out.look_lut)}</span> (Lumetri Color &gt; Creative &gt; Look &gt; Browse)`);
    }
    if (out.used_shot_analysis === false) lines.push(`<span class="muted">Tip: Analyse shots first so the cut prefers sharp, well-exposed shots with visible faces and alternates close and wide.</span>`);
    for (const w of out.warnings || []) lines.push(`<span class="fail">Note</span> ${esc(w)}`);
    for (const n of out.notes || []) lines.push(`<span class="muted">${esc(n)}</span>`);
    if (out.preview && out.preview.path) lines.push(`Preview video: <span class="pos">${esc(out.preview.path)}</span>${out.preview.imported ? " (also in bin AutoSync)" : ""}`);
    el.className = `summary ${out.imported || (!out.xml_path && out.preview) ? "ok" : "bad"}`;
    el.innerHTML = lines.join("<br>");
  }

  async function makeCut() {
    if (!state.result) throw new Error("Analyse the clips first.");
    $("cutOut").className = "summary";
    $("cutOut").textContent = "Building the first cut…";
    const out = await firstcut.make(state.jobId, cutOptions());
    state.cut = { sequence_name: out.sequence_name, xml_path: out.xml_path, sequenceGuid: out.sequenceGuid, imported: out.imported,
      stats: out.stats, warnings: out.warnings, sections: out.sections, tempo_bpm: out.tempo_bpm, notes: out.notes,
      colour: out.colour || null, look_lut: out.look_lut || null, used_shot_analysis: out.used_shot_analysis };
    persist();
    renderCut(state.cut);
  }

  async function cutVariation() {
    state.cutSeed += 1;
    await makeCut();
  }

  async function cutPreview() {
    if (!state.result) throw new Error("Analyse the clips first.");
    $("cutOut").className = "summary";
    $("cutOut").textContent = "Rendering the preview…";
    const p = await firstcut.preview(state.jobId, cutOptions(), (st) => { $("cutOut").textContent = `Rendering the preview: shot ${st.done}/${st.total}`; });
    state.cut = Object.assign({}, state.cut || {}, { preview: p });
    persist();
    renderCut(state.cut);
    if (p.note) banner("warn", esc(p.note));
  }

  // ------------------------------------------------------------------ self-test
  function renderSelftest(out) {
    const el = $("selftestOut");
    if (!out) { el.innerHTML = ""; return; }
    const row = (label, ok, detail) => `<div class="row"><span class="${ok ? "pass" : "fail"}">${ok ? "PASS" : "FAIL"}</span><span class="grow">${esc(label)}</span><span class="muted">${esc(detail)}</span></div>`;
    const b = out.spikes.b || {};
    const c = out.spikes.c || {};
    el.innerHTML = [
      `<div class="muted">Last run ${esc(out.finishedAt || out.startedAt)} · ${out.count} takes</div>`,
      row("(a) Panel reaches the helper", out.spikes.a && out.spikes.a.ok, out.spikes.a ? `helper ${out.spikes.a.version}` : ""),
      row("Engine accuracy on known takes", out.engine && !out.engine.gateFailures.length, out.engine ? `${out.engine.withinOneFramePct}% within 1 frame` : ""),
      row("(b) UXP direct placement + readback", b.ok, `${b.placed}/${b.planned} · ${b.mismatches} mismatches · ${(b.totalMs / 1000).toFixed(1)} s · longest UI block ${b.maxBlockMs == null ? "n/a" : b.maxBlockMs + " ms"}`),
      row("(c) XML import placement + readback", c.ok, c.needsManualImport ? "Premiere did not import the XML via UXP (manual import still works)" : `${c.placed}/${c.planned} · ${c.mismatches} mismatches · ${(c.totalMs / 1000).toFixed(1)} s`),
      `<div class="row"><b>Default placement method: ${out.defaultAdapter ? (out.defaultAdapter === "uxp" ? "UXP direct (A)" : "FCP7 XML import (B)") : "none passed — send the report below to support"}</b></div>`,
      ...[...(b.errors || []), ...(c.errors || [])].slice(0, 6).map((e) => `<div class="fail">${esc(e)}</div>`),
      out.savedTo ? `<div class="muted">Report saved: ${esc(out.savedTo)}</div>` : "",
    ].join("");
  }

  async function runSelftest(count) {
    $("selftestOut").textContent = "Starting…";
    const out = await selftest.run({ count, onStatus: (s) => { $("selftestOut").textContent = `${s.phase}${s.total ? ` ${s.done || 0}/${s.total}` : ""}`; } });
    renderSelftest(out);
    render();
  }

  // ------------------------------------------------------------------ render
  function render() {
    const b = state.busy;
    const helperOk = state.helper === "ok";
    $("masterName").textContent = state.masterPath ? core.baseName(state.masterPath) : "No master selected";
    $("analyseMaster").disabled = b || !helperOk || !state.masterPath;
    $("masterInfo").textContent = state.master ? `${core.formatSeconds(state.master.duration_s)} · ${state.master.sample_rate} Hz` : "";
    renderWaveform();
    const pr0 = state.probe;
    const bad = pr0 ? pr0.files.filter((f) => !f.ok).length : 0;
    $("footageInfo").textContent = state.clipPaths.length
      ? `${state.clipPaths.length} clips${pr0 ? ` · ${core.formatSeconds(pr0.total_duration_s)} total · ${pr0.files.filter((f) => f.ok && !f.has_audio).length} without audio${bad ? ` · ${bad} unreadable` : ""}` : ""}`
      : "No clips selected";
    for (const id of ["pickMaster", "pickFiles", "useSelection", "clearFootage"]) $(id).disabled = b;
    $("analyseClips").disabled = b || !helperOk || !state.masterPath || !state.clipPaths.length || polling;
    $("generate").disabled = b || !helperOk || !state.result;
    $("openSequence").disabled = b || !state.draft;
    for (const id of ["makeCut", "cutVariation", "cutPreview", "analyseShots"]) $(id).disabled = b || !helperOk || !state.result;
    $("selftestQuick").disabled = b || !helperOk;
    $("selftestFull").disabled = b || !helperOk;
    const def = storage.getItem("autosync.defaultAdapter");
    $("adapterNote").textContent = $("adapter").value === "auto"
      ? (def ? `self-test chose ${def === "uxp" ? "UXP direct" : "XML import"}` : "run the self-test first")
      : "";
    renderProgress();
    renderReview();
  }

  // ------------------------------------------------------------------ wiring
  function on(id, label, fn) {
    $(id).addEventListener("click", () => guarded(label, fn));
  }

  function init() {
    restoreSettings();
    on("pickMaster", "Select master", pickMaster);
    on("analyseMaster", "Analyse Music", analyseMaster);
    on("pickFiles", "Import files", pickFiles);
    on("useSelection", "Use selected project items", useSelection);
    on("clearFootage", "Clear footage", async () => { state.clipPaths = []; state.probe = null; persist(); });
    on("analyseClips", "Analyse All Clips", analyseClips);
    on("cancelJob", "Cancel", async () => { await api.cancel(state.jobId); });
    on("resumeJob", "Resume", async () => { await api.resume(state.jobId); banner(null); pollJob(); });
    on("generate", "Generate first draft", generate);
    on("openSequence", "Open sequence", openSequence);
    on("verifyImported", "Verify imported sequence", verifyImported);
    on("analyseShots", "Analyse shots", analyseShots);
    on("makeCut", "Make first cut", makeCut);
    on("cutVariation", "Another variation", cutVariation);
    on("cutPreview", "Render preview video", cutPreview);
    on("selftestQuick", "Placement self-test", () => runSelftest(24));
    on("selftestFull", "Placement self-test", () => runSelftest(200));
    on("saveToken", "Save token", async () => { api.setToken($("tokenInput").value); await checkHelper(); });
    $("reviewList").addEventListener("click", onReviewClick);
    $("filter").addEventListener("change", renderReview);
    for (const id of ["keepScratch", "muteScratch", "placeLow", "rate2", "fps", "res", "adapter", "cutStyle", "cutPace", "cutEffects", "cutColour", "cutLook"]) $(id).addEventListener("change", () => { persist(); render(); });
    renderSelftest(selftest.last());
    renderCut(state.cut);
    render();
    checkHelper().then(async () => {
      if (state.helper === "ok" && state.jobId) {
        try { state.job = await api.job(state.jobId); if (state.job.state === "done") state.result = await api.result(state.jobId); } catch (e) { state.jobId = null; persist(); }
        if (state.result && api.shotsStatus) {
          try { state.shots = await api.shotsStatus(state.jobId); renderShots(state.shots); } catch (e) { state.shots = null; } // 404 = not run yet
        }
        if (state.job && (state.job.state === "running" || state.job.state === "queued")) pollJob();
        render();
      }
    });
    (deps.setInterval || setInterval)(() => { if (!state.busy) checkHelper(); }, 5000);
  }

  return { init, state, pollJob, render };
}

module.exports = { createView };
