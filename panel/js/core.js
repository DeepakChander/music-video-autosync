"use strict";
/*
 * Pure logic shared by both placement adapters. No Premiere calls, no network: runs in Node tests.
 *
 * Ticks: Premiere time unit, 254016000000 per second. Value VERIFIED from Adobe's official sample
 * (AdobeDocs/uxp-premiere-pro-samples, sample-panels/premiere-api/src/sequence.ts) -- see
 * docs/PREMIERE_API_NOTES.md item 8. Ticks are BigInt here and strings at the API boundary,
 * because TickTime.createWithTicks takes a string and doubles lose precision after ~9.85 h.
 */

const TPS = 254016000000n;

function ticksPerFrame(fpsNum, fpsDen) {
  const n = TPS * BigInt(fpsDen);
  const d = BigInt(fpsNum);
  if (d <= 0n || n % d !== 0n) throw new Error(`frame rate ${fpsNum}/${fpsDen} has no whole number of ticks per frame`);
  return n / d;
}

function secondsToTicks(s) {
  return BigInt(Math.round(Number(s) * 254016000000));
}

function ticksToSeconds(t) {
  return Number(BigInt(t)) / 254016000000;
}

function timecode(ticks, fpsNum, fpsDen) {
  const tpf = ticksPerFrame(fpsNum, fpsDen);
  let t = BigInt(ticks);
  const neg = t < 0n;
  if (neg) t = -t;
  const frames = Number(t / tpf);
  const base = Math.round(fpsNum / fpsDen);
  const ff = frames % base;
  const totalS = Math.floor(frames / base);
  const pad = (n) => String(n).padStart(2, "0");
  return `${neg ? "-" : ""}${pad(Math.floor(totalS / 3600))}:${pad(Math.floor(totalS / 60) % 60)}:${pad(totalS % 60)}:${pad(ff)}`;
}

function normPath(p) {
  return String(p || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

function baseName(p) {
  const s = String(p || "").replace(/\\/g, "/");
  return s.slice(s.lastIndexOf("/") + 1);
}

/* Which clips go on the timeline (mirrors engine/overrides.py placeable, docs/DECISIONS.md D-010):
 * HIGH, MEDIUM, any override; LOW only when the user asks. UNSYNCED never unless overridden. */
function placeable(c, placeLow) {
  if (c.override) return true;
  if (c.status === "HIGH" || c.status === "MEDIUM") return true;
  return Boolean(placeLow) && c.status === "LOW";
}

function effectivePosition(c) {
  if (c.override) {
    return { startTicks: BigInt(c.override.song_position_ticks), track: c.override.video_track, inTicks: 0n, override: true };
  }
  return {
    startTicks: BigInt(c.song_position_ticks),
    track: c.video_track,
    inTicks: BigInt(c.source_in_ticks || "0"),
    override: false,
  };
}

function sourceFrameTicks(c, seqTpf) {
  if (!c.source_fps) return seqTpf;
  const [n, d] = String(c.source_fps).split("/");
  try {
    return ticksPerFrame(Number(n), Number(d || 1));
  } catch (e) {
    return seqTpf; // non-integer tick rate: fall back to sequence frames for tolerance only
  }
}

/*
 * Build the placement plan from sync_result.json. The plan is the only thing adapters place;
 * positions come from the engine (or an override) and are never recomputed here.
 */
function buildPlan(result, opts) {
  const o = Object.assign({ placeLow: false, keepScratch: true, muteScratch: true, sequenceName: "AutoSync" }, opts || {});
  const seq = result.sequence;
  const tpf = ticksPerFrame(seq.fps_num, seq.fps_den);
  const items = [];
  const unsynced = [];
  const review = [];
  const errors = [];
  let maxEnd = secondsToTicks(result.master.duration_s);
  for (const c of result.clips) {
    if (!placeable(c, o.placeLow)) {
      if (c.status === "UNSYNCED") unsynced.push({ clipId: c.clip_id, name: c.name, path: c.path, reason: c.reason, detail: c.reason_detail });
      else review.push({ clipId: c.clip_id, name: c.name, path: c.path, status: c.status, reason: c.reason });
      continue;
    }
    const pos = effectivePosition(c);
    if (!pos.track || pos.track < 1) {
      errors.push({ clipId: c.clip_id, name: c.name, error: "no video track assigned" });
      continue;
    }
    if (pos.startTicks % tpf !== 0n) {
      errors.push({ clipId: c.clip_id, name: c.name, error: `position ${pos.startTicks} is not on a sequence frame` });
      continue;
    }
    const durationTicks = secondsToTicks(c.duration_s);
    const end = pos.startTicks + (durationTicks - pos.inTicks);
    if (end > maxEnd) maxEnd = end;
    items.push({
      clipId: c.clip_id,
      name: c.name,
      path: c.path,
      status: c.status,
      override: pos.override,
      rate: c.rate || 1,
      hasVideo: Boolean(c.source_fps),
      hasAudio: Boolean(c.has_audio) && o.keepScratch,
      hasAudioSource: Boolean(c.has_audio),
      startTicks: pos.startTicks,
      inTicks: pos.inTicks,
      durationTicks,
      vIdx: pos.track - 1,          // 0-based video track (V1 = 0); VERIFIED 0-based, notes item 5
      aIdx: pos.track,              // scratch audio on A(track+1); A1 (index 0) holds the master
      inTolerance: sourceFrameTicks(c, tpf) / 2n,
    });
  }
  items.sort((a, b) => (a.vIdx - b.vIdx) || (a.startTicks < b.startTicks ? -1 : a.startTicks > b.startTicks ? 1 : 0) || a.name.localeCompare(b.name));
  const videoTracks = items.reduce((m, it) => Math.max(m, it.vIdx + 1), 0);
  // Far point for creating new tracks without rippling anything: beyond every clip and the
  // master, plus ten minutes, on a frame boundary.
  const far = ((maxEnd + secondsToTicks(600)) / tpf + 1n) * tpf;
  return {
    sequenceName: o.sequenceName,
    fpsNum: seq.fps_num,
    fpsDen: seq.fps_den,
    width: seq.width,
    height: seq.height,
    ticksPerFrame: tpf,
    master: { path: result.master.path, durationTicks: secondsToTicks(result.master.duration_s) },
    items,
    unsynced,
    review,
    errors,
    videoTracks,
    scratchTracks: o.keepScratch ? videoTracks : 0,
    muteScratch: o.muteScratch && o.keepScratch,
    farTicks: far,
    placeLow: o.placeLow,
  };
}

/*
 * Compare what Premiere reports after placement with the plan. Every mismatch is reported; the
 * placement is a success only when this returns ok (spec: never report success without readback).
 * observed = { video: [{trackIndex, startTicks, inTicks, projectItemId, name}], audio: [...],
 *              mutedAudio: [bool per audio track] }
 * idByClip  = Map clipId -> Premiere project item id
 */
function compareReadback(plan, observed, idByClip, masterId) {
  const mismatches = [];
  const used = new Set();
  const key = (side, i) => `${side}:${i}`;
  for (const it of plan.items) {
    const pid = idByClip.get(it.clipId);
    if (!pid) {
      mismatches.push({ clipId: it.clipId, name: it.name, kind: "not_in_project" });
      continue;
    }
    if (it.hasVideo) {
      const idx = observed.video.findIndex((v, i) => !used.has(key("v", i)) && v.projectItemId === pid);
      if (idx < 0) {
        mismatches.push({ clipId: it.clipId, name: it.name, kind: "video_missing", expected: String(it.startTicks) });
      } else {
        used.add(key("v", idx));
        const v = observed.video[idx];
        if (BigInt(v.startTicks) !== it.startTicks) {
          mismatches.push({ clipId: it.clipId, name: it.name, kind: "video_start", expected: String(it.startTicks), actual: String(v.startTicks),
            frames: Number((BigInt(v.startTicks) - it.startTicks) / plan.ticksPerFrame) });
        }
        if (v.trackIndex !== it.vIdx) {
          mismatches.push({ clipId: it.clipId, name: it.name, kind: "video_track", expected: it.vIdx + 1, actual: v.trackIndex + 1 });
        }
        if (it.inTicks > 0n && v.inTicks != null) {
          const d = BigInt(v.inTicks) - it.inTicks;
          if (d > it.inTolerance || -d > it.inTolerance) {
            mismatches.push({ clipId: it.clipId, name: it.name, kind: "in_point", expected: String(it.inTicks), actual: String(v.inTicks) });
          }
        }
      }
    }
    if (it.hasAudio) {
      const idx = observed.audio.findIndex((a, i) => !used.has(key("a", i)) && a.projectItemId === pid && a.trackIndex > 0);
      if (idx < 0) {
        mismatches.push({ clipId: it.clipId, name: it.name, kind: "scratch_audio_missing", expected: String(it.startTicks) });
      } else {
        used.add(key("a", idx));
        const a = observed.audio[idx];
        if (BigInt(a.startTicks) !== it.startTicks) {
          mismatches.push({ clipId: it.clipId, name: it.name, kind: "scratch_audio_start", expected: String(it.startTicks), actual: String(a.startTicks) });
        }
      }
    }
  }
  const masterIdx = observed.audio.findIndex((a) => a.projectItemId === masterId && a.trackIndex === 0);
  if (masterIdx < 0) mismatches.push({ name: "master", kind: "master_missing" });
  else {
    used.add(key("a", masterIdx));
    if (BigInt(observed.audio[masterIdx].startTicks) !== 0n) mismatches.push({ name: "master", kind: "master_start", actual: observed.audio[masterIdx].startTicks });
  }
  const extra = [];
  observed.video.forEach((v, i) => { if (!used.has(key("v", i))) extra.push({ side: "video", track: v.trackIndex + 1, name: v.name, startTicks: v.startTicks }); });
  observed.audio.forEach((a, i) => { if (!used.has(key("a", i))) extra.push({ side: "audio", track: a.trackIndex + 1, name: a.name, startTicks: a.startTicks }); });
  for (const e of extra) mismatches.push({ name: e.name, kind: "unexpected_item", actual: `${e.side} track ${e.track} at ${e.startTicks}` });
  const unmuted = [];
  if (plan.muteScratch) {
    for (let i = 1; i <= plan.scratchTracks; i++) if (observed.mutedAudio && observed.mutedAudio[i] === false) unmuted.push(i + 1);
    for (const t of unmuted) mismatches.push({ name: `A${t}`, kind: "scratch_track_not_muted" });
  }
  return { ok: mismatches.length === 0, mismatches, checked: plan.items.length };
}

/* Choose the default adapter from self-test reports: only adapters with zero mismatches and zero
 * errors qualify; the faster one wins (docs/DECISIONS.md D-002). */
function pickDefaultAdapter(reports) {
  const ok = (reports || []).filter((r) => r && r.ok && r.placed > 0);
  if (!ok.length) return null;
  ok.sort((a, b) => a.totalMs - b.totalMs);
  return ok[0].adapter;
}

function counts(result) {
  const c = { HIGH: 0, MEDIUM: 0, LOW: 0, UNSYNCED: 0 };
  for (const clip of result.clips) c[clip.status] = (c[clip.status] || 0) + 1;
  return c;
}

function formatSeconds(s) {
  if (s == null || !isFinite(s)) return "-";
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(2).padStart(5, "0")}`;
}

/* Parse a typed position: "hh:mm:ss:ff" (timecode), "mm:ss.sss", or seconds. Returns seconds or
 * null when the text is not a position. */
function parsePosition(text, fpsNum, fpsDen) {
  const t = String(text || "").trim();
  if (!t) return null;
  let m = /^(\d+):(\d{1,2}):(\d{1,2})[:;](\d{1,3})$/.exec(t);
  if (m) {
    const base = Math.round(fpsNum / fpsDen);
    const frames = ((Number(m[1]) * 60 + Number(m[2])) * 60 + Number(m[3])) * base + Number(m[4]);
    if (Number(m[4]) >= base) return null;
    return Number(ticksPerFrame(fpsNum, fpsDen) * BigInt(frames)) / 254016000000;
  }
  m = /^(\d+):(\d+(?:\.\d+)?)$/.exec(t);
  if (m) return Number(m[1]) * 60 + Number(m[2]);
  m = /^\d+(?:\.\d+)?$/.exec(t);
  if (m) return Number(t);
  return null;
}

module.exports = {
  parsePosition,
  TPS, ticksPerFrame, secondsToTicks, ticksToSeconds, timecode, normPath, baseName, placeable,
  effectivePosition, buildPlan, compareReadback, pickDefaultAdapter, counts, formatSeconds,
};
