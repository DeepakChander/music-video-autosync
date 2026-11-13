"""autosync-helper entry point.

  autosync-helper              tray app on Windows, plain server elsewhere
  autosync-helper serve        run the HTTP service in the foreground
  autosync-helper tray         tray icon + service (Windows)
  autosync-helper init-token   create the pairing token (installer runs this)
  autosync-helper print-token  print the token (for pasting into the panel)
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import multiprocessing
import os
import socket
import sys

from .config import HelperConfig, app_data_dir, ensure_token, token_path


def setup_logging() -> str:
    log_dir = os.path.join(app_data_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "helper.log")
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:
        root.addHandler(logging.StreamHandler())
    return path


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def build_server(cfg: HelperConfig):
    import uvicorn

    from .app import create_app
    token = ensure_token()
    app = create_app(token, cfg.cache_dir, cfg.workers)
    # 127.0.0.1 only: the helper is never reachable from another machine.
    config = uvicorn.Config(app, host="127.0.0.1", port=cfg.port, log_level="info", access_log=False,
                            log_config=None)
    return uvicorn.Server(config)


def cmd_serve(cfg: HelperConfig) -> int:
    if not port_free(cfg.port):
        logging.error("port %s is already in use (is the helper already running?)", cfg.port)
        return 3
    logging.info("helper listening on http://127.0.0.1:%s, cache %s", cfg.port, cfg.cache_dir)
    build_server(cfg).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    p = argparse.ArgumentParser(prog="autosync-helper")
    p.add_argument("command", nargs="?", choices=["serve", "tray", "init-token", "print-token"])
    p.add_argument("--port", type=int)
    p.add_argument("--regenerate", action="store_true", help="with init-token: make a new token")
    a = p.parse_args(argv)
    if a.command == "init-token":
        ensure_token(regenerate=a.regenerate)
        print(token_path())
        return 0
    if a.command == "print-token":
        print(ensure_token())
        return 0
    setup_logging()
    cfg = HelperConfig.load()
    if a.port:
        cfg.port = a.port
    command = a.command or ("tray" if os.name == "nt" else "serve")
    if command == "tray":
        from .tray import run_tray
        return run_tray(cfg)
    return cmd_serve(cfg)


if __name__ == "__main__":
    sys.exit(main())
