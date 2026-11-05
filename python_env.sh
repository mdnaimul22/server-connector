#!/usr/bin/env sh
# Shared helper: make the real Python 3 win over the Microsoft Store stubs
# and provide a single find_python3() for every script in this repo.

if [ -n "${LOCALAPPDATA:-}" ] && command -v cygpath >/dev/null 2>&1; then
  local_app="$(cygpath -u "$LOCALAPPDATA")"
  for py_dir in "$local_app"/Programs/Python/Python3*; do
    if [ -d "$py_dir" ]; then
      PATH="$py_dir:$py_dir/Scripts:$PATH"
    fi
  done
  unset local_app py_dir
fi

find_python3() {
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}
