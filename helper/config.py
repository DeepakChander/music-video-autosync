"""Paths, port and the pairing token. No network access beyond 127.0.0.1."""

from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import dataclass

APP_NAME = "MusicVideoAutoSync"
DEFAULT_PORT = 47653
TOKEN_HEADER = "X-AutoSync-Token"


def app_data_dir() -> str:
    """Roaming app data: holds the token and config. The panel reads the token from
    <home>/AppData/Roaming/MusicVideoAutoSync/token on Windows."""
    env = os.environ.get("AUTOSYNC_HOME")
    if env:
        return env
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME)


def default_cache_dir() -> str:
    env = os.environ.get("AUTOSYNC_CACHE")
    if env:
        return env
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, APP_NAME, "cache")


def token_path() -> str:
    return os.path.join(app_data_dir(), "token")


def ensure_token(regenerate: bool = False) -> str:
    """Create the token at install time (or first start). 32 random bytes, URL-safe."""
    path = token_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not regenerate and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            tok = f.read().strip()
        if len(tok) >= 32:
            return tok
    tok = secrets.token_urlsafe(32)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(tok)
    os.replace(tmp, path)
    if os.name != "nt":
        os.chmod(path, 0o600)
    return tok


@dataclass
class HelperConfig:
    port: int = DEFAULT_PORT
    cache_dir: str = ""
    workers: int | None = None

    @classmethod
    def load(cls) -> "HelperConfig":
        cfg = cls(cache_dir=default_cache_dir())
        path = os.path.join(app_data_dir(), "config.json")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.port = int(data.get("port", cfg.port))
            cfg.cache_dir = data.get("cache_dir") or cfg.cache_dir
            cfg.workers = data.get("workers")
        if os.environ.get("AUTOSYNC_PORT"):
            cfg.port = int(os.environ["AUTOSYNC_PORT"])
        return cfg
