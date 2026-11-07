#!/bin/sh
# Double-click (macOS: rename to .command if needed) or run: ./start-mac-linux.sh
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then exec python3 -m subkeep "$@"; fi
if command -v python >/dev/null 2>&1; then exec python -m subkeep "$@"; fi
echo "SubKeep needs Python 3.9 or newer: https://www.python.org/downloads/"
