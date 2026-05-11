#!/usr/bin/env bash
# s-trans launcher for macOS and Linux.
#   - installs `uv` if missing (curl or wget)
#   - syncs Python deps
#   - warns about missing system binaries (LibreOffice, Tesseract, Ghostscript)
#   - builds the web UI if not already built and Node >=20 is available
#   - finds a free port if the default is taken
#   - starts the server and opens the browser
set -euo pipefail

# Refuse to run under non-bash shells (we rely on /dev/tcp and arrays).
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script requires bash. Run it as: ./run.sh   (not 'sh run.sh')" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "$SCRIPT_DIR"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-7860}"

c_red()   { printf '\033[31m%s\033[0m\n' "$*"; }
c_green() { printf '\033[32m%s\033[0m\n' "$*"; }
c_yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }
c_blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

# Friendly error trap: tell the user which step failed.
on_err() {
  local exit_code=$?
  local line=$1
  c_red ""
  c_red "==> Launcher failed (line $line, exit $exit_code)."
  c_red "    See messages above. Common causes:"
  c_red "      - No internet access (uv/deps download)"
  c_red "      - Disk full or no write permission in $(pwd)"
  c_red "      - Corporate proxy blocking pypi.org / astral.sh"
  c_red "      - A transitive Python dep with no wheel for your OS/arch"
  exit "$exit_code"
}
trap 'on_err $LINENO' ERR

OS="$(uname -s)"
case "$OS" in
  Darwin) PLATFORM="mac" ;;
  Linux)  PLATFORM="linux" ;;
  *) c_red "Unsupported OS: $OS (use run.ps1 on Windows)"; exit 1 ;;
esac

c_blue "==> s-trans launcher ($PLATFORM, $(uname -m))"

# --- uv ---------------------------------------------------------------------
install_uv() {
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    c_red "Neither curl nor wget is installed. Install one of them, or install uv manually:"
    c_red "  https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
  fi
  # uv's installer drops an env file that updates PATH.
  for envf in "$HOME/.local/bin/env" "$HOME/.cargo/env"; do
    [ -f "$envf" ] && . "$envf" || true
  done
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
}

if ! command -v uv >/dev/null 2>&1; then
  c_yellow "uv not found — installing ..."
  install_uv
fi
if ! command -v uv >/dev/null 2>&1; then
  c_red "uv install completed but 'uv' is still not on PATH."
  c_red "Open a new terminal and re-run, or add ~/.local/bin to your PATH."
  exit 1
fi
c_green "uv: $(uv --version)"

# --- system binaries (warn-only) -------------------------------------------
missing_bins=()
check_bin() {
  local bin="$1" label="$2"
  if command -v "$bin" >/dev/null 2>&1; then
    c_green "$label: $(command -v "$bin")"
  else
    c_yellow "$label: NOT FOUND ($bin)"
    missing_bins+=("$label")
  fi
}

if [[ "$PLATFORM" == "mac" ]] && ! command -v soffice >/dev/null 2>&1; then
  if [[ -x "/Applications/LibreOffice.app/Contents/MacOS/soffice" ]]; then
    export PATH="/Applications/LibreOffice.app/Contents/MacOS:$PATH"
  fi
fi

check_bin soffice   "LibreOffice (soffice)"
check_bin tesseract "Tesseract OCR"
check_bin gs        "Ghostscript"

if [[ ${#missing_bins[@]} -gt 0 ]]; then
  c_yellow ""
  c_yellow "Some optional system binaries are missing. The app will run, but"
  c_yellow "PDF OCR and .doc/.ppt conversion may fail. Install them with:"
  if [[ "$PLATFORM" == "mac" ]]; then
    echo "    brew install --cask libreoffice"
    echo "    brew install tesseract tesseract-lang ghostscript"
  else
    if command -v apt-get >/dev/null 2>&1; then
      echo "    sudo apt-get install -y libreoffice tesseract-ocr tesseract-ocr-ara ghostscript"
    elif command -v dnf >/dev/null 2>&1; then
      echo "    sudo dnf install -y libreoffice tesseract tesseract-langpack-ara ghostscript"
    elif command -v pacman >/dev/null 2>&1; then
      echo "    sudo pacman -S --needed libreoffice-fresh tesseract tesseract-data-ara ghostscript"
    else
      echo "    (install libreoffice, tesseract + arabic langpack, ghostscript via your package manager)"
    fi
  fi
  c_yellow ""
fi

# --- .env -------------------------------------------------------------------
if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
  c_green "Created .env from .env.example — edit it to add provider keys."
fi

# Export everything in .env so provider keys (DEEPSEEK_API_KEY, OPENAI_API_KEY,
# …) are visible to LiteLLM, which reads them from os.environ. pydantic-settings
# alone wouldn't propagate them.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# --- Python deps ------------------------------------------------------------
c_blue "==> Installing Python dependencies (uv sync) ..."
uv sync --quiet

# --- web UI -----------------------------------------------------------------
need_ui_build=0
if [[ ! -f app/web/dist/index.html ]]; then need_ui_build=1; fi

if [[ "$need_ui_build" == "1" ]]; then
  if command -v npm >/dev/null 2>&1; then
    node_major=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
    if [[ "$node_major" -lt 20 ]]; then
      c_yellow "Node.js >=20 required to build the UI (found v$(node -v 2>/dev/null || echo none))."
      c_yellow "Skipping UI build — install Node 20+ and re-run if you need the web UI."
    else
      c_blue "==> Building web UI ..."
      (cd app/web && npm install --silent && npm run build --silent)
    fi
  else
    c_yellow "app/web/dist/index.html missing and npm is not installed."
    c_yellow "The API will still work but the web UI won't be served."
    c_yellow "Install Node.js >=20 and re-run, or use a release build that ships dist/."
  fi
fi

# --- pick a free port -------------------------------------------------------
port_in_use() {
  local p=$1
  (echo >/dev/tcp/127.0.0.1/"$p") &>/dev/null
}
if port_in_use "$PORT"; then
  orig=$PORT
  for cand in 7861 7862 7863 7870 8000 8080 8888; do
    if ! port_in_use "$cand"; then PORT=$cand; break; fi
  done
  if [[ "$PORT" == "$orig" ]]; then
    c_red "Port $orig is in use and no fallback port is free. Set PORT=<n> and re-run."
    exit 1
  fi
  c_yellow "Port $orig was in use — using $PORT instead."
fi
URL="http://${HOST}:${PORT}"

# --- launch -----------------------------------------------------------------
open_browser() {
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then open "$URL" >/dev/null 2>&1 || true
  fi
}

(
  for _ in $(seq 1 60); do
    if (echo >/dev/tcp/127.0.0.1/"$PORT") &>/dev/null; then
      open_browser
      exit 0
    fi
    sleep 0.5
  done
) &
opener_pid=$!
trap 'kill "$opener_pid" 2>/dev/null || true' EXIT

c_green "==> Starting s-trans on $URL  (Ctrl+C to stop)"
export HOST PORT
exec uv run python -m app.main
