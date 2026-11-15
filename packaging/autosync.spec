# PyInstaller one-folder build: AutoSyncHelper.exe (windowed tray + service) and
# autosync-engine.exe (console CLI) share one folder. FFmpeg is copied next to them into
# ffmpeg\ by the release workflow (engine/probe.py looks there).
# pystray is LGPL-3.0: collected as plain .py files so it can be replaced (docs/LICENSES.md).
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "packaging", "autosync.ico")
HIDDEN = (collect_submodules("uvicorn") + collect_submodules("engine") + collect_submodules("helper")
          + collect_submodules("export") + ["tools.make_synthetic_takes", "tools.synth_song", "tools.accuracy_report"])
EXCLUDES = ["tkinter", "matplotlib", "opentimelineio", "pytest", "IPython"]

# YuNet face model (MIT) next to engine/shots.py, where it looks for it.
DATAS = [(os.path.join(ROOT, "engine", "models", "face_detection_yunet_2023mar.onnx"), os.path.join("engine", "models"))]
# Ad first cut: python-docx templates, whisper.cpp native libraries.
DATAS += collect_data_files("docx")
BINARIES = collect_dynamic_libs("pywhispercpp")
HIDDEN += (collect_submodules("pywhispercpp") + collect_submodules("rapidfuzz") + collect_submodules("openpyxl")
           + collect_submodules("pypdf") + ["docx", "lxml.etree", "_pywhispercpp"])

a_helper = Analysis([os.path.join(ROOT, "packaging", "helper_entry.py")], pathex=[ROOT], hiddenimports=HIDDEN + ["pystray._win32", "cv2"],
                    excludes=EXCLUDES, datas=DATAS, binaries=BINARIES, module_collection_mode={"pystray": "py"})
a_engine = Analysis([os.path.join(ROOT, "packaging", "engine_entry.py")], pathex=[ROOT], hiddenimports=HIDDEN + ["cv2"],
                    datas=DATAS, binaries=BINARIES, excludes=EXCLUDES + ["pystray"])


def _no_opencv_ffmpeg(entries):
    """OpenCV's wheel bundles an LGPL FFmpeg DLL (opencv_videoio_ffmpeg*.dll) for its video I/O.
    AutoSync never decodes through OpenCV (FFmpeg runs as a separate process), so it is left out
    (docs/LICENSES.md)."""
    return [e for e in entries if "opencv_videoio_ffmpeg" not in os.path.basename(e[0]).lower()]


a_helper.binaries = _no_opencv_ffmpeg(a_helper.binaries)
a_engine.binaries = _no_opencv_ffmpeg(a_engine.binaries)

pyz_helper = PYZ(a_helper.pure)
exe_helper = EXE(pyz_helper, a_helper.scripts, [], exclude_binaries=True, name="AutoSyncHelper", console=False, icon=ICON)
pyz_engine = PYZ(a_engine.pure)
exe_engine = EXE(pyz_engine, a_engine.scripts, [], exclude_binaries=True, name="autosync-engine", console=True, icon=ICON)

coll = COLLECT(exe_helper, a_helper.binaries, a_helper.datas, exe_engine, a_engine.binaries, a_engine.datas,
               name="MusicVideoAutoSync")
