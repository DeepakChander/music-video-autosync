# Phase 5 gate - packaging

GitHub Actions run 43463627820 (`.github/workflows/release.yml`, windows-latest runner, commit 33eac3c),
2026-11-13. Every step passed.

| Step | Evidence | Result |
|---|---|---|
| LGPL FFmpeg | BtbN asset `ffmpeg-n9.0-latest-win64-lgpl-9.0.zip`, `ffmpeg version n9.0.2-57-gd838f9f9ae-20261113`; build refused if configured with `--enable-gpl` | PASS |
| PyInstaller one-folder build (AutoSyncHelper.exe + autosync-engine.exe) | `autosync-engine.exe doctor` finds the bundled ffmpeg/ffprobe | PASS |
| Panel `.ccx` | `packaging/build_ccx.py` validation (manifest, icons, requires), 33 192 bytes | PASS |
| Installer | Inno Setup, `MusicVideoAutoSync-Setup-0.1.0.exe`, 134 605 172 bytes | PASS |
| Silent install | Program files, ffmpeg, `.ccx`, uninstaller, pairing token, start-with-Windows shortcut all present | PASS |
| Installed helper end to end | `AutoSyncHelper.exe serve` + `tools/helper_smoke.py --count 14`: helper used `...\Programs\MusicVideoAutoSync\ffmpeg\ffmpeg.exe`; 14 self-test takes generated and analysed in 98.5 s; 100% within 1 frame, 0 wrong HIGH/MEDIUM; XML exported; 7/7 checks | PASS |
| Silent uninstall | Program folder, `%APPDATA%\MusicVideoAutoSync`, `%LOCALAPPDATA%\MusicVideoAutoSync` and Startup shortcut all gone | PASS |

Caveats, stated plainly:
- A GitHub runner is not a pristine client machine (it has developer tools installed). The helper
  used only its bundled FFmpeg (path above), but a real clean-machine install is still the client's
  first step (`docs/CLIENT_SMOKE_TEST.md`).
- The runner has no Premiere Pro or Creative Cloud, so the panel install through Adobe's
  UnifiedPluginInstallerAgent was skipped there (the installer checks for it). Not verified.
- The build is not code-signed.
