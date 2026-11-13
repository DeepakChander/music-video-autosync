"use strict";
/*
 * In-memory stand-in for require("premierepro"), shaped after the API surface in
 * docs/PREMIERE_API_NOTES.md. It is NOT Premiere: tests using it prove the panel's call logic
 * (locks, transactions, track creation, marks, readback), not Premiere's behaviour. Knobs let a
 * test switch documented-unknown behaviours (e.g. whether overwrite creates tracks).
 */

const fs = require("fs");

const TPS = 254016000000n;
let nextId = 1;
const newId = (p) => `${p}-${nextId++}`;

function createFake(knobs) {
  const k = Object.assign({
    overwriteCreatesTracks: false,  // undocumented: default to the pessimistic case
    xmlImport: "creates",           // "creates" | "none"
    defaultVideoTracks: 3,
    defaultAudioTracks: 3,
    shiftOneClipBy: 0n,             // fault injection: misplace the Nth placed clip
    shiftClipIndex: -1,
    unimportable: new Set(),
    mediaInfo: new Map(),           // path -> { durationTicks, hasVideo, hasAudio }
  }, knobs || {});

  let inLock = false;
  const log = [];
  const requireLock = (what) => { if (!inLock) throw new Error(`${what} created outside lockedAccess`); };

  class TickTime {
    constructor(t) { this.ticks = String(t); this.ticksNumber = Number(t); this.seconds = Number(t) / 254016000000; }
    static createWithTicks(s) { if (typeof s !== "string") throw new Error("createWithTicks takes a string"); return new TickTime(BigInt(s)); }
    static createWithSeconds(x) { return new TickTime(BigInt(Math.round(x * 254016000000))); }
  }
  TickTime.TIME_ZERO = new TickTime(0n);

  class FrameRate {
    constructor(v) { this.value = v; this.ticksPerFrame = Math.round(254016000000 / v); }
    static createWithValue(v) { return new FrameRate(v); }
  }

  class RectF { constructor() { this.width = 0; this.height = 0; } }

  const Constants = { TrackItemType: { EMPTY: 0, CLIP: 1 }, MediaType: { ANY: "any", VIDEO: "video", AUDIO: "audio" } };

  class Action {
    constructor(desc, fn) { this.desc = desc; this.fn = fn; }
  }

  class ProjectItem {
    constructor(name) { this.name = name; this._id = newId("pi"); this._parent = null; }
    getId() { return this._id; }
    getParentBin() { return this._parent; }
    static cast(x) { return x instanceof ProjectItem ? x : null; }
  }

  class FolderItem extends ProjectItem {
    constructor(name) { super(name); this.children = []; }
    async getItems() { return this.children.slice(); }
    createBinAction(name, makeUnique) {
      requireLock("createBinAction");
      return new Action(`bin ${name}`, () => { const f = new FolderItem(name); f._parent = this; this.children.push(f); });
    }
    createMoveItemAction(item, newParent) {
      requireLock("createMoveItemAction");
      if (item._parent !== this) throw new Error("createMoveItemAction must be called on the current parent");
      return new Action(`move ${item.name}`, () => {
        this.children = this.children.filter((c) => c !== item);
        newParent.children.push(item);
        item._parent = newParent;
      });
    }
    static cast(x) { return x instanceof FolderItem ? x : null; }
  }

  class ClipProjectItem extends ProjectItem {
    constructor(path, info) {
      super(path.replace(/\\/g, "/").split("/").pop());
      this.path = path;
      this.info = info;
      this.inTicks = 0n;
      this.outTicks = info.durationTicks;
    }
    async getMediaFilePath() { return this.path; }
    async getInPoint() { return new TickTime(this.inTicks); }
    async getOutPoint() { return new TickTime(this.outTicks); }
    createSetInOutPointsAction(i, o) {
      requireLock("createSetInOutPointsAction");
      return new Action(`marks ${this.name}`, () => { this.inTicks = BigInt(i.ticks); this.outTicks = BigInt(o.ticks); });
    }
    static cast(x) { return x instanceof ClipProjectItem ? x : null; }
  }

  class SequenceProjectItem extends ProjectItem {}

  class TrackItem {
    constructor(clip, start, inTicks, length, kind) { this.clip = clip; this.start = start; this.inTicks = inTicks; this.length = length; this.kind = kind; }
    get end() { return this.start + this.length; }
    async getStartTime() { return new TickTime(this.start); }
    async getEndTime() { return new TickTime(this.end); }
    async getInPoint() { return new TickTime(this.inTicks); }
    async getProjectItem() { return this.clip; }
    async getName() { return this.clip.name; }
    createMoveAction(delta) {
      requireLock("createMoveAction");
      return new Action(`move item ${this.clip.name}`, () => { this.start += BigInt(delta.ticks); });
    }
  }

  class Track {
    constructor(kind) { this.kind = kind; this.items = []; this.muted = false; }
    getTrackItems(type) { if (type !== Constants.TrackItemType.CLIP) throw new Error("only CLIP supported in fake"); return this.items.slice().sort((a, b) => (a.start < b.start ? -1 : 1)); }
    async setMute(m) { this.muted = Boolean(m); return true; }
    async isMuted() { return this.muted; }
    overwrite(item) {
      // remove or trim what the new item covers (overwrite semantics)
      const keep = [];
      for (const o of this.items) {
        if (o.end <= item.start || o.start >= item.end) keep.push(o);
        else if (o.start < item.start) { o.length = item.start - o.start; keep.push(o); }
        else if (o.end > item.end) { const cut = item.end - o.start; o.start = item.end; o.inTicks += cut; o.length -= cut; keep.push(o); }
      }
      keep.push(item);
      this.items = keep;
    }
    rippleFrom(t, by) { for (const o of this.items) if (o.start >= t) o.start += by; }
  }

  class SequenceSettings {
    constructor(seq) { this.seq = seq; this.pending = null; }
    getVideoFrameRate() { return { value: 254016000000 / Number(this.seq.tpf), ticksPerFrame: Number(this.seq.tpf) }; }
    setVideoFrameRate(fr) { this.pending = BigInt(Math.round(Number(fr.ticksPerFrame))); return true; }
    async setVideoFrameRect(r) { this.rect = { w: r.width, h: r.height }; return true; }
  }

  class Sequence {
    constructor(name, project) {
      this.name = name;
      this.guid = newId("guid");
      this.tpf = 10160640000n; // default preset: 25 fps
      this.video = Array.from({ length: k.defaultVideoTracks }, () => new Track("video"));
      this.audio = Array.from({ length: k.defaultAudioTracks }, () => new Track("audio"));
      this.player = 0n;
      this.pi = new SequenceProjectItem(name);
      this.project = project;
    }
    async getSettings() { return new SequenceSettings(this); }
    createSetSettingsAction(settings) {
      requireLock("createSetSettingsAction");
      return new Action("settings", () => { if (settings.pending) this.tpf = settings.pending; });
    }
    async getVideoTrackCount() { return this.video.length; }
    async getAudioTrackCount() { return this.audio.length; }
    async getVideoTrack(i) { return this.video[i]; }
    async getAudioTrack(i) { return this.audio[i]; }
    async getProjectItem() { return this.pi; }
    async setPlayerPosition(t) { this.player = BigInt(t.ticks); return true; }
    snapshot() {
      return JSON.stringify({ v: this.video.map((t) => t.items.map((i) => [i.clip.path, String(i.start), String(i.length)])),
        a: this.audio.map((t) => t.items.map((i) => [i.clip.path, String(i.start), String(i.length)])) });
    }
  }

  let placedCount = 0;
  class SequenceEditor {
    constructor(seq) { this.seq = seq; }
    static getEditor(seq) { return new SequenceEditor(seq); }
    _items(pi, time) {
      const clip = pi;
      const len = clip.outTicks - clip.inTicks;
      let start = BigInt(time.ticks);
      if (clip.info.hasVideo || clip.info.hasAudio) {
        if (placedCount === k.shiftClipIndex) start += k.shiftOneClipBy;
      }
      placedCount += 1;
      return { start, len, clip };
    }
    createOverwriteItemAction(pi, time, v, a) {
      requireLock("createOverwriteItemAction");
      return new Action(`overwrite ${pi.name}`, () => {
        const { start, len, clip } = this._items(pi, time);
        const needV = clip.info.hasVideo, needA = clip.info.hasAudio;
        if (needV && v >= this.seq.video.length) { if (!k.overwriteCreatesTracks) throw new Error(`no video track ${v}`); this.seq.video.push(new Track("video")); }
        if (needA && a >= this.seq.audio.length) { if (!k.overwriteCreatesTracks) throw new Error(`no audio track ${a}`); this.seq.audio.push(new Track("audio")); }
        if (needV) this.seq.video[v].overwrite(new TrackItem(clip, start, clip.inTicks, len, "video"));
        if (needA) this.seq.audio[a].overwrite(new TrackItem(clip, start, clip.inTicks, len, "audio"));
      });
    }
    createInsertProjectItemAction(pi, time, v, a, limitShift) {
      requireLock("createInsertProjectItemAction");
      return new Action(`insert ${pi.name}`, () => {
        const { start, len, clip } = this._items(pi, time);
        const needV = clip.info.hasVideo, needA = clip.info.hasAudio;
        if (needV && v === this.seq.video.length) this.seq.video.push(new Track("video"));
        if (needA && a === this.seq.audio.length) this.seq.audio.push(new Track("audio"));
        if ((needV && v > this.seq.video.length) || (needA && a > this.seq.audio.length)) throw new Error("track index skips a track");
        // insert ripples the target tracks, and (sync lock, limitShift unreliable) every track
        for (const t of [...this.seq.video, ...this.seq.audio]) t.rippleFrom(start, len);
        if (needV) this.seq.video[v].items.push(new TrackItem(clip, start, clip.inTicks, len, "video"));
        if (needA) this.seq.audio[a].items.push(new TrackItem(clip, start, clip.inTicks, len, "audio"));
      });
    }
  }

  // Minimal XML tree parser for the machine-written xmeml (no comments/CDATA).
  function parseXml(text) {
    const root = { tag: "#root", attrs: {}, children: [], text: "" };
    const stack = [root];
    const re = /<\?[^>]*\?>|<!DOCTYPE[^>]*>|<\/([\w:.-]+)\s*>|<([\w:.-]+)((?:\s+[\w:.-]+="[^"]*")*)\s*(\/?)>|([^<]+)/g;
    let m;
    while ((m = re.exec(text))) {
      if (m[1]) { stack.pop(); continue; }
      if (m[2]) {
        const attrs = {};
        for (const a of (m[3] || "").matchAll(/([\w:.-]+)="([^"]*)"/g)) attrs[a[1]] = a[2];
        const node = { tag: m[2], attrs, children: [], text: "" };
        stack[stack.length - 1].children.push(node);
        if (!m[4]) stack.push(node);
        continue;
      }
      if (m[5] && m[5].trim()) stack[stack.length - 1].text += m[5];
    }
    return root;
  }
  const kid = (n, tag) => n && n.children.find((c) => c.tag === tag);
  const kids = (n, tag) => (n ? n.children.filter((c) => c.tag === tag) : []);
  const txt = (n, tag) => { const c = kid(n, tag); return c ? c.text.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">") : null; };

  function parseXmlSequence(text, project) {
    const xm = kid(parseXml(text), "xmeml");
    const sq = kid(xm, "sequence");
    const rate = kid(sq, "rate");
    const tb = BigInt(txt(rate, "timebase"));
    const tpf = txt(rate, "ntsc") === "TRUE" ? (TPS * 1001n) / (tb * 1000n) : TPS / tb;
    const seq = new Sequence(txt(sq, "name"), project);
    seq.tpf = tpf;
    seq.video = [];
    seq.audio = [];
    const files = {};
    const decode = (u) => decodeURIComponent(u.replace(/^file:\/\/localhost/, "").replace(/^\/([A-Za-z])%3a\//i, "$1:/"));
    const walkFiles = (n) => { for (const c of n.children) { if (c.tag === "file" && kid(c, "pathurl")) files[c.attrs.id] = decode(txt(c, "pathurl")); walkFiles(c); } };
    walkFiles(sq);
    const media = kid(sq, "media");
    const load = (block, kind, into) => {
      for (const tr of kids(block, "track")) {
        const t = new Track(kind);
        for (const ci of kids(tr, "clipitem")) {
          const f = kid(ci, "file");
          const clip = project._findClip(files[f.attrs.id]);
          if (!clip) throw new Error(`XML references media not in project: ${files[f.attrs.id]}`);
          const n = (tag) => BigInt(txt(ci, tag));
          t.items.push(new TrackItem(clip, n("start") * tpf, n("in") * tpf, (n("end") - n("start")) * tpf, kind));
        }
        into.push(t);
      }
    };
    // Premiere imports the media referenced by the XML before building the sequence.
    for (const p of Object.values(files)) if (!project._findClip(p)) {
      const info = k.mediaInfo.get(p) || { durationTicks: 10n * TPS, hasVideo: true, hasAudio: true };
      const c = new ClipProjectItem(p, info);
      c._parent = project.root;
      project.root.children.push(c);
    }
    load(kid(media, "video"), "video", seq.video);
    load(kid(media, "audio"), "audio", seq.audio);
    return seq;
  }

  class Project {
    constructor() {
      this.root = new FolderItem("root");
      this.sequences = [];
      this.active = null;
      this.opened = [];
      this.selection = [];
    }
    static async getActiveProject() { return project; }
    lockedAccess(cb) { inLock = true; try { cb(); } finally { inLock = false; } }
    executeTransaction(cb, undo) {
      if (!inLock) throw new Error("executeTransaction outside lockedAccess");
      const actions = [];
      cb({ addAction(a) { if (!(a instanceof Action)) throw new Error("not an Action"); actions.push(a); return true; }, get empty() { return actions.length === 0; } });
      for (const a of actions) a.fn();
      log.push(undo);
      return true;
    }
    async getRootItem() { return this.root; }
    _findClip(path) {
      const want = path.replace(/\\/g, "/").toLowerCase();
      const walk = (f) => { for (const c of f.children) { if (c instanceof ClipProjectItem && c.path.replace(/\\/g, "/").toLowerCase() === want) return c; if (c instanceof FolderItem) { const r = walk(c); if (r) return r; } } return null; };
      return walk(this.root);
    }
    async importFiles(paths, suppressUI, targetBin, stills) {
      const bin = targetBin || this.root;
      let all = true;
      for (const p of paths) {
        if (/\.xml$/i.test(p)) {
          if (k.xmlImport !== "creates") { all = false; continue; }
          const seq = parseXmlSequence(fs.readFileSync(p, "utf8"), this);
          this.sequences.push(seq);
          seq.pi._parent = bin;
          bin.children.push(seq.pi);
          continue;
        }
        if (k.unimportable.has(p)) { all = false; continue; }
        const info = k.mediaInfo.get(p) || { durationTicks: 10n * TPS, hasVideo: true, hasAudio: true };
        const c = new ClipProjectItem(p, info);
        c._parent = bin;
        bin.children.push(c);
      }
      return all;
    }
    async createSequence(name) {
      const s = new Sequence(name, this);
      this.sequences.push(s);
      s.pi._parent = this.root;
      this.root.children.push(s.pi);
      return s;
    }
    async getSequences() { return this.sequences.slice(); }
    async getActiveSequence() { return this.active; }
    async setActiveSequence(s) { this.active = s; return true; }
    async openSequence(s) { this.opened.push(s.guid); return true; }
  }

  const project = new Project();
  const ProjectUtils = { async getSelection() { return { async getItems() { return project.selection.slice(); } }; } };

  const ppro = { Project, TickTime, FrameRate, RectF, Constants, ProjectItem, FolderItem, ClipProjectItem, SequenceEditor, ProjectUtils };
  return { ppro, project, log, knobs: k, Sequence, Track, TrackItem };
}

module.exports = { createFake, TPS };
