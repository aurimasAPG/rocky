#!/bin/sh
# Rocky installer — macOS / Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/aurimasAPG/rocky/main/install.sh | sh
#
# What it does:
#   1. installs uv (https://astral.sh/uv) if it is missing
#   2. installs Rocky as an isolated uv tool from Git (or a local checkout)
#   3. makes sure ~/.local/bin is on your PATH
#
# Knobs (environment variables):
#   ROCKY_REPO     git URL            default https://github.com/aurimasAPG/rocky
#   ROCKY_REF      branch / tag       default main
#   ROCKY_SOURCE   local checkout     install from a folder instead of Git
#   ROCKY_EXTRAS   "browser"          also install Playwright tools
#   ROCKY_PYTHON   3.12               Python version uv should use

set -eu

REPO="${ROCKY_REPO:-https://github.com/aurimasAPG/rocky}"
REF="${ROCKY_REF:-main}"
PY="${ROCKY_PYTHON:-3.12}"
EXTRAS="${ROCKY_EXTRAS:-}"
BIN_DIR="${UV_TOOL_BIN_DIR:-$HOME/.local/bin}"

say()  { printf '\033[1;38;5;209m%s\033[0m %s\n' "rocky" "$*"; }
fail() { printf '\033[1;31m%s\033[0m %s\n' "rocky" "$*" >&2; exit 1; }

case "$(uname -s)" in
  Darwin|Linux) ;;
  MINGW*|MSYS*|CYGWIN*) fail "on Windows run:  irm https://raw.githubusercontent.com/aurimasAPG/rocky/main/install.ps1 | iex" ;;
  *) fail "unsupported OS: $(uname -s)" ;;
esac

command -v curl >/dev/null 2>&1 || fail "curl is required"

# 1. uv
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  say "installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi
PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
export PATH
command -v uv >/dev/null 2>&1 || fail "uv did not install; see https://docs.astral.sh/uv/"

# 2. Rocky
if [ -n "${ROCKY_SOURCE:-}" ]; then
  SPEC="$ROCKY_SOURCE"
  say "installing from $ROCKY_SOURCE"
else
  SPEC="git+${REPO}@${REF}"
  say "installing from ${REPO} (${REF})"
fi
if [ -n "$EXTRAS" ]; then
  SPEC="rocky[$EXTRAS] @ $SPEC"
fi

LOG="$(mktemp 2>/dev/null || echo /tmp/rocky-install.log)"
if ! uv tool install --force --reinstall --python "$PY" "$SPEC" >"$LOG" 2>&1; then
  cat "$LOG" >&2
  fail "uv tool install failed (log above)"
fi
rm -f "$LOG"

# 3. PATH
if ! command -v rocky >/dev/null 2>&1; then
  uv tool update-shell >/dev/null 2>&1 || true
fi

VERSION="$("$BIN_DIR/rocky" --version 2>/dev/null || "$HOME/.local/bin/rocky" --version 2>/dev/null || echo "installed")"
say "$VERSION"

if [ "$EXTRAS" = "browser" ]; then
  say "installing Chromium for the browser tools…"
  uv tool run --from playwright playwright install chromium >/dev/null 2>&1 || say "run later:  playwright install chromium"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) say "add to your shell profile:  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

cat <<EOF

  Next:
    1. cd into a project folder and run:   rocky
       (it asks for your OpenRouter key once and saves it to ~/.env)
    2. check the setup:                    rocky doctor
    3. guide:                              rocky help
    4. update Rocky:                       rocky update

EOF
