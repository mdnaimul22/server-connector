#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
. ./python_env.sh

AUTOSTART=0
for arg in "$@"; do
  case "$arg" in
    --autostart) AUTOSTART=1 ;;
    *)
      echo "Unknown option: $arg" >&2
      echo "Usage: ./install.sh [--autostart]" >&2
      exit 2
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Platform detection happens once. Every install step afterwards is an
# explicit per-platform branch, so adding a new OS only means adding a case.
# ---------------------------------------------------------------------------
detect_platform() {
  case "$(uname -s 2>/dev/null || echo unknown)" in
    Linux*)
      if [ -d /data/data/com.termux ] || [ -n "${TERMUX_VERSION:-}" ]; then
        echo termux
      else
        echo linux
      fi
      ;;
    Darwin*) echo darwin ;;
    MINGW*|MSYS*|CYGWIN*) echo windows ;;
    *) echo unknown ;;
  esac
}

install_termux() {
  pkg install -y python curl
  # proot + dig only exist for the Termux DNS workaround.
  for pkg_item in proot:proot dig:dnsutils; do
    bin_name="${pkg_item%%:*}"
    pkg_name="${pkg_item#*:}"
    if ! command -v "$bin_name" >/dev/null 2>&1; then
      pkg install -y "$pkg_name" || true
    fi
  done
}

install_linux() {
  if command -v apt-get >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then
      apt-get update && apt-get install -y python3 curl
    elif command -v sudo >/dev/null 2>&1; then
      sudo apt-get update && sudo apt-get install -y python3 curl
    else
      echo "Install python3 and curl, then rerun." >&2
      return 1
    fi
  elif command -v dnf >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then
      dnf install -y python3 curl
    elif command -v sudo >/dev/null 2>&1; then
      sudo dnf install -y python3 curl
    else
      echo "Install python3 and curl, then rerun." >&2
      return 1
    fi
  elif command -v pacman >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then
      pacman -Sy --noconfirm python curl
    elif command -v sudo >/dev/null 2>&1; then
      sudo pacman -Sy --noconfirm python curl
    else
      echo "Install python and curl, then rerun." >&2
      return 1
    fi
  else
    echo "Install Python 3 and curl with your package manager." >&2
    return 1
  fi
}

install_darwin() {
  if command -v brew >/dev/null 2>&1; then
    brew install python3 || true
  else
    echo "Install Python 3 from https://www.python.org/downloads/ and rerun." >&2
  fi
}

install_windows() {
  echo "Python 3 was not found. On Windows install both of these, then rerun:" >&2
  echo "  winget install --id Python.Python.3.13 -e" >&2
  echo "  winget install --id Git.Git -e   (provides bash, chmod and coreutils)" >&2
}

PLATFORM="$(detect_platform)"
echo "Detected platform: $PLATFORM"

if ! PYTHON="$(find_python3)"; then
  case "$PLATFORM" in
    termux) install_termux ;;
    linux) install_linux ;;
    darwin) install_darwin ;;
    windows)
      install_windows
      exit 1
      ;;
    *)
      echo "Unsupported platform; install Python 3 manually." >&2
      exit 1
      ;;
  esac
  PYTHON="$(find_python3)" || {
    echo "Python 3 is still unavailable; install it and rerun." >&2
    exit 1
  }
fi

case "$PLATFORM" in
  windows)
    if ! command -v bash >/dev/null 2>&1; then
      echo "NOTE: Git Bash was not found; run_command needs it. Install Git for Windows." >&2
    fi
    ;;
  termux)
    if ! command -v proot >/dev/null 2>&1; then
      echo "NOTE: proot is missing; the ngrok DNS workaround will be skipped." >&2
    fi
    ;;
esac

mkdir -p logs
"$PYTHON" engine.py init-config
chmod +x start.sh stop.sh install.sh status.sh autostart.sh 2>/dev/null || true

if [ "$AUTOSTART" -eq 1 ]; then
  echo "Registering logon autostart for this platform..."
  if ! "$PYTHON" engine.py autostart-install; then
    echo "WARNING: autostart could not be registered; run ./autostart.sh install later." >&2
  fi
fi

echo "Install complete. Edit config.yml if needed, then start with: ./start.sh"
