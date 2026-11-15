#!/usr/bin/env bash
# Let the Premiere panel reach the helper on macOS.
#
# Premiere disallows plain http:// URLs on macOS (Adobe's Premiere UXP "Network Operations"
# recipe: "Connection fails only on macOS - HTTPS required"). This script makes the helper serve
# https://127.0.0.1:47653 with a certificate signed by a local certificate authority that mkcert
# creates on THIS Mac and adds to the macOS keychain (macOS asks for your password once).
# The certificate is valid only for 127.0.0.1 and localhost. Undo: see docs/MAC_GUIDE.md.
#
#   ./scripts/setup_mac_https.sh
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=lib_mac.sh
. scripts/lib_mac.sh
use_brew || true  # Homebrew installed but not on PATH yet: use it anyway

if ! command -v mkcert >/dev/null 2>&1; then
  ensure_brew mkcert || { echo "mkcert is needed (Homebrew installs it)."; exit 1; }
  echo "Installing mkcert with Homebrew..."
  "$BREW" install mkcert
fi
if [ ! -x .venv/bin/python ]; then
  echo "Run ./scripts/setup_mac.sh first."; exit 1
fi

HOME_DIR="$(.venv/bin/python -c 'from helper.config import app_data_dir; print(app_data_dir())')"
TLS_DIR="$HOME_DIR/tls"
mkdir -p "$TLS_DIR"

echo "Adding mkcert's local certificate authority to the macOS keychain (password prompt)..."
mkcert -install
mkcert -cert-file "$TLS_DIR/helper-cert.pem" -key-file "$TLS_DIR/helper-key.pem" 127.0.0.1 localhost

.venv/bin/python - "$HOME_DIR" "$TLS_DIR" <<'PY'
import json, os, sys
home, tls = sys.argv[1], sys.argv[2]
path = os.path.join(home, "config.json")
cfg = json.load(open(path)) if os.path.isfile(path) else {}
cfg["tls_cert"] = os.path.join(tls, "helper-cert.pem")
cfg["tls_key"] = os.path.join(tls, "helper-key.pem")
json.dump(cfg, open(path, "w"), indent=2)
print("Wrote", path)
PY

cat <<MSG

HTTPS ready. Restart the helper (Ctrl+C in its window, then ./scripts/start_helper_mac.sh).
It now says: listening on https://127.0.0.1:47653
MSG
