#!/bin/sh
# Serves the WebMIDI tools (tools/web/) on localhost, where Chrome allows WebMIDI with SysEx. Bundles the preset
# bank there first (tools/web/presets.json, git-ignored), for the Patches tab's "Install the preset bank", and copies
# the current build (tools/web/firmware.syx, git-ignored) for the installer, install.html.
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
syx="$root/build/pro800-$(cat "$root/VERSION").syx"
if [ -f "$syx" ]; then
  cp "$syx" "$root/tools/web/firmware.syx"
else
  rm -f "$root/tools/web/firmware.syx"
  echo "no $(basename "$syx") yet: run scripts/build.sh for the installer's firmware" >&2
fi
echo "http://localhost:$port/ (the installer: http://localhost:$port/install.html)"
exec "$PYTHON" -m http.server "$port" --bind 127.0.0.1 --directory "$root/tools/web"
