# PyInstaller one-folder build: AutoSyncHelper.exe (windowed tray + service) and
# autosync-engine.exe (console CLI) share one folder. FFmpeg is copied next to them into
# ffmpeg\ by the release workflow (engine/probe.py looks there).
# pystray is LGPL-3.0: collected as plain .py files so it can be replaced (docs/LICENSES.md).
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "packaging", "autosync.ico")
HIDDEN = (collect_submodules("uvicorn") + collect_submodules("engine") + collect_submodules("helper")
          + collect_submodules("export") + ["tools.make_synthetic_takes", "tools.synth_song", "tools.accuracy_report"])
EXCLUDES = ["tkinter", "matplotlib", "opentimelineio", "pytest", "IPython"]

a_helper = Analysis([os.path.join(ROOT, "packaging", "helper_entry.py")], pathex=[ROOT], hiddenimports=HIDDEN + ["pystray._win32"],
                    excludes=EXCLUDES, module_collection_mode={"pystray": "py"})
a_engine = Analysis([os.path.join(ROOT, "packaging", "engine_entry.py")], pathex=[ROOT], hiddenimports=HIDDEN, excludes=EXCLUDES + ["pystray"])

pyz_helper = PYZ(a_helper.pure)
exe_helper = EXE(pyz_helper, a_helper.scripts, [], exclude_binaries=True, name="AutoSyncHelper", console=False, icon=ICON)
pyz_engine = PYZ(a_engine.pure)
exe_engine = EXE(pyz_engine, a_engine.scripts, [], exclude_binaries=True, name="autosync-engine", console=True, icon=ICON)

coll = COLLECT(exe_helper, a_helper.binaries, a_helper.datas, exe_engine, a_engine.binaries, a_engine.datas,
               name="MusicVideoAutoSync")
