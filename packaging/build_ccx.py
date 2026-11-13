"""Package the UXP panel as a .ccx (a plain zip with manifest.json at the archive root,
forward-slash entry names -- docs/PREMIERE_API_NOTES.md item 11).

Before zipping it checks that the manifest parses, that main and every icon exist (including @2x
variants for scale 2), and that every relative require() in the shipped JS resolves to a shipped
file. Exit code 1 on any problem.

  python packaging/build_ccx.py [--out packaging/out]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(ROOT, "panel")
SKIP_DIRS = {"test", "node_modules"}
SKIP_FILES = {"package.json", ".DS_Store"}


def shipped_files() -> list[str]:
    out = []
    for root, dirs, files in os.walk(PANEL):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if f in SKIP_FILES:
                continue
            out.append(os.path.relpath(os.path.join(root, f), PANEL).replace(os.sep, "/"))
    return out


def check(files: list[str]) -> list[str]:
    problems = []
    fs = set(files)
    with open(os.path.join(PANEL, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    if m.get("manifestVersion") != 5:
        problems.append("manifestVersion must be 5 for Premiere")
    if not isinstance(m.get("host"), dict) or m["host"].get("app") != "premierepro":
        problems.append("host must be a single object with app 'premierepro' for packaging")
    if m.get("main") not in fs:
        problems.append(f"main {m.get('main')} not shipped")
    icons = [i for e in m.get("entrypoints", []) for i in e.get("icons", [])] + m.get("icons", [])
    for ic in icons:
        p = ic["path"]
        if p not in fs:
            problems.append(f"icon missing: {p}")
        if 2 in ic.get("scale", []):
            stem, ext = os.path.splitext(p)
            if f"{stem}@2x{ext}" not in fs:
                problems.append(f"icon missing: {stem}@2x{ext}")
    for f in files:
        if not f.endswith(".js"):
            continue
        with open(os.path.join(PANEL, f), encoding="utf-8") as fh:
            src = fh.read()
        for req in re.findall(r"require\(\s*[\"'](\.[^\"']+)[\"']\s*\)", src):
            # index.js is at the root; modules under js/ do not require relative files.
            target = os.path.normpath(os.path.join(os.path.dirname(f), req)).replace(os.sep, "/")
            if target not in fs:
                problems.append(f"{f}: require('{req}') -> {target} not shipped")
            if not req.endswith(".js"):
                problems.append(f"{f}: require('{req}') must name the .js file (UXP does not resolve folders)")
            if f != "index.js":
                problems.append(f"{f}: only index.js may require relative modules (UXP resolution rules)")
    return problems


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=os.path.join(ROOT, "packaging", "out"))
    a = p.parse_args(argv)
    files = shipped_files()
    problems = check(files)
    if problems:
        for x in problems:
            print("ERROR", x)
        return 1
    with open(os.path.join(PANEL, "manifest.json"), encoding="utf-8") as f:
        version = json.load(f)["version"]
    os.makedirs(a.out, exist_ok=True)
    out = os.path.join(a.out, "MusicVideoAutoSync_premierepro.ccx")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(os.path.join(PANEL, f), f)
    print(json.dumps({"ccx": out, "version": version, "files": len(files)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
