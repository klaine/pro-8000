#!/bin/sh
# Serves the WebMIDI tools (tools/web/) on localhost, where Chrome allows WebMIDI with SysEx. Bundles the preset
# bank there first (tools/web/presets.json, git-ignored), for the Patches tab's "Install the preset bank".
#
# Usage: scripts/web.sh [-h|--help] [port]
#
#   then open http://localhost:<port>/ (default 8800) in Chrome or Edge; Safari has no WebMIDI.
#
# Environment: PYTHON (default python3).
set -eu

case "${1:-}" in
    -h|--help) sed -n '2,/^set -eu/{/^set -eu/d;s/^# \{0,1\}//;p;}' "$0"; exit 0 ;;
esac

PYTHON=${PYTHON:-python3}
port=${1:-8800}
root=$(cd "$(dirname "$0")/.." && pwd)
"$PYTHON" -I "$root/presets/lib/bundle.py" "$root/tools/web/presets.json"
echo "http://localhost:$port/"
exec "$PYTHON" -m http.server "$port" --bind 127.0.0.1 --directory "$root/tools/web"
