#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
. ./python_env.sh
PYTHON="$(find_python3)" || { echo "ERROR: Python 3 is required" >&2; exit 1; }
exec "$PYTHON" engine.py status
