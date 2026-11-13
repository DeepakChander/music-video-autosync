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
  const base = `http://127.0.0.1:${port}`;
  let token = deps.token || null;

  async function loadToken() {
    if (token) return token;
    if (deps.storage) {
      const saved = deps.storage.getItem("autosync.token");
      if (saved) { token = saved; return token; }
    }
    if (deps.fs && deps.os) {
      const home = String(deps.os.homedir()).replace(/\\/g, "/");
      const path = `${home}/AppData/Roaming/MusicVideoAutoSync/token`;
      try {
        token = String(await deps.fs.readFile(path, { encoding: "utf-8" })).trim();
        return token;
      } catch (e) {
        throw new HelperError("TOKEN_NOT_FOUND", `Pairing token not found at ${path}. Paste it from the helper's tray menu (Copy pairing token).`, 0);
      }
    }
    throw new HelperError("TOKEN_NOT_FOUND", "No pairing token. Paste it from the helper's tray menu.", 0);
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
    let res;
    try {
      res = await fetchFn(base + path, {
        method,
        headers: { [TOKEN_HEADER]: tok, "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw new HelperError("HELPER_DOWN", `Helper not running on ${base} (${e && e.message ? e.message : e})`, 0);
    }
    let data = null;
    const text = await res.text();
    try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { error: { code: "BAD_RESPONSE", message: text.slice(0, 200) } }; }
    if (!res.ok) {
      const err = (data && data.error) || {};
      throw new HelperError(err.code || `HTTP_${res.status}`, err.message || `HTTP ${res.status}`, res.status);
    }
    return data;
  }

  return {
    base,
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
  };
}

module.exports = { createApi, HelperError, DEFAULT_PORT, TOKEN_HEADER };
