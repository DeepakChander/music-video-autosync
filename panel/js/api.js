"use strict";
/*
 * Client for the local helper (127.0.0.1 only). Every request carries the pairing token.
 * fetch is the UXP global; manifest grants network "domains": "all" because Premiere rejects
 * "http://127.0.0.1:<port>" entries (docs/PREMIERE_API_NOTES.md item 10, SECONDARY evidence).
 *
 * Token: written by the installer to %APPDATA%\MusicVideoAutoSync\token. Read with
 * require("fs").readFile + require("os").homedir() (VERIFIED: uxp-api/reference-js/modules/fs/fs,
 * .../modules/os/os; needs localFileSystem "fullAccess"). If that fails, the user pastes it.
 */

const DEFAULT_PORT = 47653;
const TOKEN_HEADER = "X-AutoSync-Token";

class HelperError extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

function createApi(deps) {
  const fetchFn = deps.fetch;
  const port = deps.port || DEFAULT_PORT;
  // Windows: plain http to 127.0.0.1. macOS: Premiere disallows http:// URLs (Adobe's Premiere
  // UXP network recipe), so the Mac helper can serve https with a locally trusted certificate
  // (scripts/setup_mac_https.sh). The first base that answers is remembered.
  const bases = deps.bases || [`http://127.0.0.1:${port}`, `https://127.0.0.1:${port}`, `https://localhost:${port}`];
  let base = bases[0];
  let token = deps.token || null;

  // Where the helper writes the token (helper/config.py app_data_dir) on Windows, macOS, Linux.
  function tokenPaths(home) {
    return [
      `${home}/AppData/Roaming/MusicVideoAutoSync/token`,
      `${home}/Library/Application Support/MusicVideoAutoSync/token`,
      `${home}/.config/MusicVideoAutoSync/token`,
    ];
  }

  // The helper's token file wins (a reinstall writes a new one); a pasted token is the fallback.
  async function loadToken() {
    if (token) return token;
    let tried = [];
    if (deps.fs && deps.os) {
      const home = String(deps.os.homedir()).replace(/\\/g, "/");
      tried = tokenPaths(home);
      for (const path of tried) {
        try {
          const t = String(await deps.fs.readFile(path, { encoding: "utf-8" })).trim();
          if (t) { token = t; return token; }
        } catch (e) { /* try the next location, then a pasted token */ }
      }
    }
    const saved = deps.storage ? deps.storage.getItem("autosync.token") : null;
    if (saved) { token = saved; return token; }
    throw new HelperError("TOKEN_NOT_FOUND", `Pairing token not found${tried.length ? ` (looked in ${tried.join(", ")})` : ""}. ` +
      "Paste it into the panel: Windows tray menu > Copy pairing token, or on a Mac run: .venv/bin/python -m helper print-token", 0);
  }

  function setToken(t) {
    token = String(t || "").trim() || null;
    if (deps.storage) {
      if (token) deps.storage.setItem("autosync.token", token);
      else deps.storage.removeItem("autosync.token");
    }
  }

  async function call(method, path, body) {
    const tok = await loadToken();
    const init = {
      method,
      headers: { [TOKEN_HEADER]: tok, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    };
    let res = null;
    const errors = [];
    for (const b of [base, ...bases.filter((x) => x !== base)]) {
      try {
        res = await fetchFn(b + path, init);
        base = b;
        break;
      } catch (e) {
        errors.push(`${b}: ${e && e.message ? e.message : e}`);
      }
    }
    if (!res) throw new HelperError("HELPER_DOWN", `Helper not reachable (${errors.join("; ")})`, 0);
    let data = null;
    const text = await res.text();
    try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { error: { code: "BAD_RESPONSE", message: text.slice(0, 200) } }; }
    if (!res.ok) {
      const err = (data && data.error) || {};
      if (res.status === 401) token = null; // re-read the token file on the next call
      throw new HelperError(err.code || `HTTP_${res.status}`, err.message || `HTTP ${res.status}`, res.status);
    }
    return data;
  }

  return {
    get base() { return base; },
    loadToken,
    setToken,
    health: () => call("GET", "/health"),
    settingsDefaults: () => call("GET", "/settings/defaults"),
    master: (path) => call("POST", "/master", { path, peaks: 240 }),
    probe: (paths, folders) => call("POST", "/probe", { paths: paths || [], folders: folders || [] }),
    createJob: (masterPath, clipPaths, sequence, settings) => call("POST", "/jobs", { master_path: masterPath, clip_paths: clipPaths, sequence, settings: settings || {} }),
    job: (id) => call("GET", `/jobs/${encodeURIComponent(id)}`),
    jobs: () => call("GET", "/jobs"),
    cancel: (id) => call("POST", `/jobs/${encodeURIComponent(id)}/cancel`),
    resume: (id) => call("POST", `/jobs/${encodeURIComponent(id)}/resume`),
    result: (id) => call("GET", `/jobs/${encodeURIComponent(id)}/result`),
    override: (id, clipId, songPositionS, by, note) => call("POST", `/jobs/${encodeURIComponent(id)}/clips/${encodeURIComponent(clipId)}/override`,
      { song_position_s: songPositionS, by: by || "editor", note: note || null }),
    exportXml: (id, opts) => call("POST", `/jobs/${encodeURIComponent(id)}/export/fcp7xml`, opts || {}),
    selftest: (count, sequence) => call("POST", "/selftest", { count, sequence }),
    accuracy: (id) => call("GET", `/jobs/${encodeURIComponent(id)}/accuracy`),
    saveReport: (id, name, report) => call("POST", `/jobs/${encodeURIComponent(id)}/reports`, { name, report }),
    music: (id) => call("GET", `/jobs/${encodeURIComponent(id)}/music`),
    cut: (id, opts) => call("POST", `/jobs/${encodeURIComponent(id)}/cut`, opts || {}),
    cutPreview: (id) => call("GET", `/jobs/${encodeURIComponent(id)}/cut/preview`),
    shots: (id, opts) => call("POST", `/jobs/${encodeURIComponent(id)}/shots`, opts || {}),
    shotsStatus: (id) => call("GET", `/jobs/${encodeURIComponent(id)}/shots`),
    adScript: (path) => call("POST", "/ad/script", { path }),
    adTemplate: (path) => call("POST", "/ad/template", { path }),
    adCreate: (req) => call("POST", "/ad/jobs", req),
    adJob: (id) => call("GET", `/ad/jobs/${encodeURIComponent(id)}`),
    adPreviewStart: (id, width) => call("POST", `/ad/jobs/${encodeURIComponent(id)}/preview`, { width }),
    adPreviewStatus: (id) => call("GET", `/ad/jobs/${encodeURIComponent(id)}/preview`),
    adSpeech: () => call("GET", "/ad/speech"),
  };
}

module.exports = { createApi, HelperError, DEFAULT_PORT, TOKEN_HEADER };
