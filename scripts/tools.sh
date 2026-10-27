#!/bin/sh
# Builds the macOS command-line tool that talks to the PRO-800 over USB MIDI.
#
# Usage: scripts/tools.sh [-h|--help]
#
#   build/pro800   see `build/pro800 help` for its commands (ports, version, listen, backup, play,
#                  update-mode, flash)
#
# Environment: SWIFTC (default swiftc).
set -eu

case "${1:-}" in
    -h|--help) sed -n '2,/^set -eu/{/^set -eu/d;s/^# \{0,1\}//;p;}' "$0"; exit 0 ;;
esac

SWIFTC=${SWIFTC:-swiftc}
root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$root/build"
"$SWIFTC" -O -file-prefix-map "$root"=. -o "$root/build/pro800" "$root/tools/pro800.swift"
echo "build/pro800"
