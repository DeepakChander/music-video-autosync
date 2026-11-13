# Install

## Editors (Windows 10/11, Premiere Pro 26.x)

1. Run `MusicVideoAutoSync-Setup-<version>.exe` (per-user, no admin rights). On the unsigned test
   build, SmartScreen asks: **More info > Run anyway**.
2. The installer:
   - puts the helper, the engine and an LGPL FFmpeg in `%LOCALAPPDATA%\Programs\MusicVideoAutoSync`;
   - creates the pairing token in `%APPDATA%\MusicVideoAutoSync\token`;
   - starts the helper with Windows (Startup shortcut), and shows a tray icon;
   - installs the Premiere panel with Adobe's plugin installer when Creative Cloud is present.
3. In Premiere: **Window > UXP Plugins > Music Video AutoSync**. If it is missing, double-click
   `MusicVideoAutoSync_premierepro.ccx` (Start menu > *Music Video AutoSync - Premiere panel (.ccx)*)
   and restart Premiere.

Then run the self-test in `docs/CLIENT_SMOKE_TEST.md`.

**Uninstall:** Settings > Apps > Music Video AutoSync. Removes the program, the panel, the token, the
logs and the analysis cache.

## Developers

```bash
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt   # or pip
# FFmpeg on PATH (or AUTOSYNC_FFMPEG_DIR=<folder with ffmpeg/ffprobe>)
.venv/bin/python -m pytest                       # engine, helper, export (56+ tests)
node --test panel/test/*.test.js                 # panel logic against a simulated Premiere
.venv/bin/python -m helper serve                 # helper on 127.0.0.1:47653
.venv/bin/python tools/helper_smoke.py           # end-to-end smoke against the running helper
.venv/bin/python packaging/build_ccx.py          # validated panel package
```

Load the panel unpacked: enable Premiere's developer mode (Settings > Plugins > Enable developer
mode, restart), then UXP Developer Tool > **Add Plugin** > `panel/manifest.json` > **Load**.

Windows build: `.github/workflows/release.yml` (workflow_dispatch, or push a `v*` tag to publish a
GitHub Release). It builds with PyInstaller, bundles FFmpeg, builds the installer with Inno Setup,
then installs it silently, runs the installed helper through `tools/helper_smoke.py`, uninstalls
silently and fails if anything is left behind.
