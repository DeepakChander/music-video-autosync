#!/usr/bin/env bash
# Start the AutoSync helper on macOS (127.0.0.1:47653). Keep this window open; Ctrl+C stops it.
#   ./scripts/start_helper_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo "Run ./scripts/setup_mac.sh first."; exit 1
fi
echo "Music Video AutoSync helper"
echo "  listening on 127.0.0.1:${AUTOSYNC_PORT:-47653}  (only this Mac can reach it)"
echo "  pairing token: .venv/bin/python -m helper print-token"
echo "  stop: Ctrl+C"
exec .venv/bin/python -m helper serve
