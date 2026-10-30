#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
export PATH="$(pwd)/bin:$PATH"
PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then echo "ERROR: Python 3 is required" >&2; exit 1; fi
"$PYTHON" engine.py stop
if command -v termux-wake-unlock >/dev/null 2>&1; then termux-wake-unlock 2>/dev/null || true; fi
echo "Doomsday Connector stopped cleanly."
