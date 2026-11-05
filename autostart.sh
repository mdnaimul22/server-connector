#!/usr/bin/env sh
# Manage "start the connector when this machine starts" per platform.
#   ./autostart.sh install | remove | status
set -eu
cd "$(dirname "$0")"
. ./python_env.sh

ACTION="${1:-status}"
case "$ACTION" in
  install|remove|status) ;;
  *)
    echo "Usage: ./autostart.sh install|remove|status" >&2
    exit 2
    ;;
esac

PYTHON="$(find_python3)" || { echo "ERROR: Python 3 is required" >&2; exit 1; }
exec "$PYTHON" engine.py "autostart-$ACTION"
