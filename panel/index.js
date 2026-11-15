"use strict";
/*
 * Entry point (loaded by index.html). Requires use full relative paths with ".js" from the plugin
 * root (UXP CommonJS rules, docs/PREMIERE_API_NOTES.md item 10). Modules do not require each
 * other: everything is injected here, which is also how the Node tests load them.
 */
const ppro = require("premierepro");
const uxp = require("uxp");
const fs = require("fs");
const os = require("os");

const core = require("./js/core.js");
const { createApi } = require("./js/api.js");
const { createPremiere } = require("./js/premiere.js");
const { createCommon } = require("./js/placement/common.js");
const { createUxpAdapter } = require("./js/placement/uxp.js");
const { createXmlAdapter } = require("./js/placement/xml.js");
const { createSelfTest } = require("./js/selftest.js");
const { createFirstCut } = require("./js/firstcut.js");
const { createCutPlacer } = require("./js/placement/cut.js");
const { createView } = require("./js/ui/view.js");

const log = {
  info: (...a) => console.log("[AutoSync]", ...a),
  warn: (...a) => console.warn("[AutoSync]", ...a),
};

const storage = window.localStorage;
const api = createApi({ fetch: (url, init) => fetch(url, init), fs, os, storage });
const pr = createPremiere(ppro, core, log);
const common = createCommon(ppro, core, pr);
const adapters = {
  uxp: createUxpAdapter(ppro, core, pr, common),
  xml: createXmlAdapter(ppro, core, pr, common, api),
};
const selftest = createSelfTest(core, api, adapters, common, storage);
const firstcut = createFirstCut(ppro, pr, api, null, createCutPlacer(ppro, core, pr, adapters.uxp));
const view = createView({
  doc: document, core, api, pr, adapters, selftest, common, storage, firstcut,
  lfs: uxp.storage.localFileSystem,
  hostVersion: uxp.host && uxp.host.version,
});
view.init();
