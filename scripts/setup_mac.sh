#!/usr/bin/env bash
# One-time setup on macOS after cloning. Installs what is missing (Homebrew after asking, then
# FFmpeg and Python 3.11 with Homebrew), the Python packages, the pairing token and the Premiere
# panel package. Safe to run again.
#   ./scripts/setup_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
# shellcheck source=lib_mac.sh
. scripts/lib_mac.sh
use_brew || true  # Homebrew installed but not on PATH yet: use it anyway

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mSetup stopped:\033[0m %s\n' "$*"; exit 1; }

have_ffmpeg() { command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; }

PY=""
find_python() {
  local c v
  PY=""
  for c in python3.11 python3.12 python3.13 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      v="$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || continue
      case "$v" in 3.11|3.12|3.13) PY="$c"; return 0 ;; esac
    fi
  done
  return 1
}

say "1/5 Checking FFmpeg and Python 3.11-3.13"
missing=()
have_ffmpeg || missing+=(ffmpeg)
if ! find_python && ! command -v uv >/dev/null 2>&1; then
  missing+=(python@3.11)  # macOS's own python3 is 3.9: too old
fi
if [ "${#missing[@]}" -gt 0 ]; then
  echo "Missing: ${missing[*]}"
  ensure_brew "${missing[*]}" || fail "Homebrew is needed to install ${missing[*]}."
  echo "Installing ${missing[*]} with Homebrew (several minutes the first time)..."
  "$BREW" install "${missing[@]}" || fail "brew install ${missing[*]} failed (see the messages above)."
  hash -r
fi
have_ffmpeg || fail "FFmpeg is still not found after installing it. Close Terminal, open it again, and run this script again."
ffmpeg -hide_banner -version | head -1

say "2/5 Setting up Python"
if find_python; then
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
  fail "Python 3.11 is still not found after installing it. Close Terminal, open it again, and run this script again."
fi

say "4/5 Creating the pairing token"
.venv/bin/python -m helper init-token

say "5/5 Building the Premiere panel package"
.venv/bin/python packaging/build_ccx.py

cat <<MSG

Setup finished.

Next (docs/MAC_GUIDE.md, from step 3):
  1. Turn on HTTPS for the panel (once):
       ./scripts/setup_mac_https.sh
  2. Start the helper (keep that Terminal window open while you use the panel):
       ./scripts/start_helper_mac.sh
  3. Install the panel: double-click
       $ROOT/packaging/out/MusicVideoAutoSync_premierepro.ccx
     then restart Premiere Pro and open Window > UXP Plugins > Music Video AutoSync.
MSG
