"""Windows tray launcher: runs the service in a thread and shows its state.

Menu: status line, Copy pairing token, Open cache folder, Open log folder, Quit.
pystray (LGPL-3.0) is imported here only and is shipped as replaceable source (docs/LICENSES.md).
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading

from .config import HelperConfig, app_data_dir, ensure_token


def _icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(30, 30, 36, 255))
    for i, h in enumerate((18, 34, 46, 28, 40, 22)):  # waveform bars
        x = 12 + i * 7
        d.rectangle((x, 32 - h // 2, x + 4, 32 + h // 2), fill=(90, 200, 160, 255))
    return img


def _copy_to_clipboard(text: str) -> None:
    if os.name == "nt":
        subprocess.run(["clip"], input=text.encode("utf-16-le"), check=False, creationflags=0x08000000)


def _open_folder(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 - opens Explorer on a local folder


def run_tray(cfg: HelperConfig) -> int:
    import pystray

    from .__main__ import build_server, port_free

    if not port_free(cfg.port):
        logging.error("port %s already in use; another helper is probably running", cfg.port)
        return 3
    server = build_server(cfg)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    status = f"Helper running on 127.0.0.1:{cfg.port}"

    def on_quit(icon, _item):
        server.should_exit = True
        thread.join(timeout=10)
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(status, None, enabled=False),
        pystray.MenuItem("Copy pairing token", lambda *_: _copy_to_clipboard(ensure_token())),
        pystray.MenuItem("Open cache folder", lambda *_: _open_folder(cfg.cache_dir)),
        pystray.MenuItem("Open log folder", lambda *_: _open_folder(os.path.join(app_data_dir(), "logs"))),
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("MusicVideoAutoSync", _icon_image(), "Music Video AutoSync helper", menu)
    icon.run()
    return 0
