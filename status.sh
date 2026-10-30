#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then echo "ERROR: Python 3 is required" >&2; exit 1; fi
exec "$PYTHON" engine.py status
