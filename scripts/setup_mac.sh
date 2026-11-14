#!/usr/bin/env bash
# One-time setup on macOS after cloning: Python environment, pairing token, Premiere panel package.
#   ./scripts/setup_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mSetup stopped:\033[0m %s\n' "$*"; exit 1; }

say "1/5 Checking FFmpeg"
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  fail "FFmpeg is not installed. Install Homebrew (https://brew.sh), then run:  brew install ffmpeg   and run this script again."
fi
ffmpeg -hide_banner -version | head -1

say "2/5 Finding Python 3.11, 3.12 or 3.13"
PY=""
for c in python3.11 python3.12 python3.13 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v="$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    case "$v" in 3.11|3.12|3.13) PY="$c"; break ;; esac
  fi
done
if [ -n "$PY" ]; then
  echo "Using $PY ($("$PY" --version))"
  [ -x .venv/bin/python ] || "$PY" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  say "3/5 Installing Python packages (a few minutes the first time)"
  .venv/bin/python -m pip install --quiet -r requirements.txt
elif command -v uv >/dev/null 2>&1; then
  echo "Using uv to fetch Python 3.11"
  [ -x .venv/bin/python ] || uv venv --python 3.11 .venv
  say "3/5 Installing Python packages (a few minutes the first time)"
  uv pip install --quiet --python .venv/bin/python -r requirements.txt
else
  fail "Python 3.11 to 3.13 is not installed (macOS's built-in python3 is too old). Run:  brew install python@3.11   and run this script again."
fi

say "4/5 Creating the pairing token"
.venv/bin/python -m helper init-token

say "5/5 Building the Premiere panel package"
.venv/bin/python packaging/build_ccx.py

cat <<MSG

Setup finished.

Next:
  1. Start the helper (keep that Terminal window open while you use the panel):
       ./scripts/start_helper_mac.sh
  2. Install the panel: double-click
       $ROOT/packaging/out/MusicVideoAutoSync_premierepro.ccx
     then restart Premiere Pro and open Window > UXP Plugins > Music Video AutoSync.
  3. Follow docs/MAC_GUIDE.md from step 4.
MSG
