# Shared by the macOS scripts (sourced, not run). Finds Homebrew even when it is not on PATH yet
# (right after installing it, Homebrew is only on PATH once its "Next steps" have run), and can
# install it with Homebrew's official installer after asking.
#
# Test hooks: AUTOSYNC_BREW_PATHS (where to look for brew), AUTOSYNC_HOMEBREW_INSTALL_URL.

HOMEBREW_INSTALL_URL="${AUTOSYNC_HOMEBREW_INSTALL_URL:-https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh}"
HOMEBREW_INSTALL_CMD="/bin/bash -c \"\$(curl -fsSL $HOMEBREW_INSTALL_URL)\""

# Prints the path of brew, or nothing.
find_brew() {
  local b
  if [ -z "${AUTOSYNC_BREW_PATHS:-}" ] && command -v brew >/dev/null 2>&1; then
    command -v brew; return 0
  fi
  for b in ${AUTOSYNC_BREW_PATHS:-/opt/homebrew/bin/brew /usr/local/bin/brew}; do
    if [ -x "$b" ]; then echo "$b"; return 0; fi
  done
  return 1
}

# Puts Homebrew's bin folder on PATH for this script. Returns 1 when Homebrew is not installed.
use_brew() {
  local b
  b="$(find_brew)" || return 1
  eval "$("$b" shellenv bash 2>/dev/null || "$b" shellenv)"
  BREW="$b"
}

# Adds Homebrew to new Terminal windows, as Homebrew's own "Next steps" ask (zsh, the macOS default).
persist_brew_path() {
  local line rc
  case "${SHELL:-}" in */zsh) ;; *) return 0 ;; esac
  rc="${ZDOTDIR:-$HOME}/.zprofile"
  line="eval \"\$($BREW shellenv zsh)\""
  if ! grep -qs "brew shellenv" "$rc"; then
    printf '\n%s\n' "$line" >> "$rc"
    echo "Added Homebrew to $rc so new Terminal windows find it."
  fi
}

# Makes sure Homebrew is available, offering to install it. $1 says what it is needed for.
ensure_brew() {
  use_brew && return 0
  printf '\nHomebrew (https://brew.sh, the standard Mac package manager) is needed to install %s.\n' "$1"
  printf 'It asks for your Mac password and may install Apple'"'"'s Command Line Tools (several minutes).\n'
  local answer="n"
  if [ -t 0 ]; then
    printf 'Install Homebrew now? [Y/n] '
    read -r answer || answer="n"
  fi
  case "$answer" in
    ""|y|Y|yes|YES)
      /bin/bash -c "$(curl -fsSL "$HOMEBREW_INSTALL_URL")" || return 1
      use_brew || return 1
      persist_brew_path
      ;;
    *)
      printf '\nInstall it yourself with:\n  %s\nthen run this script again.\n' "$HOMEBREW_INSTALL_CMD"
      return 1
      ;;
  esac
}
