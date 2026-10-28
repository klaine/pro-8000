#!/bin/sh
# presets/install.sh: load the preset bank (presets/*.json) into the PRO-800 over USB with build/pro800.
#
# Each preset starts from the init sound (`patch init`), gets its buttons, knobs, settings and mod routes, and is
# saved to its slot (P1-P64) and renamed, so running this twice gives the same result. Every step is one
# `build/pro800` command, printed as it runs.
#
# usage: presets/install.sh [--dry-run] [--only <slots>] [--tool <path>] [--port <name>]
#   --dry-run        print the commands instead of running them (no synth needed)
#   --only 1-8,62    install these slots only (ranges and lists)
#   --tool <path>    the tool (default: build/pro800 next to this folder)
#   --port <name>    MIDI port, passed to every command (default: the tool's, "PRO 800")
# Honours PYTHON= (default python3). Needs our firmware 0.8.1 or later on the synth.
#
# Step sequences (the arp group) and the morph target (P59) need two commands the tool doesn't have yet
# (`seq set` / `seq step` and `set morph`; see presets/README.md). Until it has them they're skipped with a
# warning; everything else in those patches is installed, and a rerun fills them in once the tool knows them.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$here")
tool="$root/build/pro800"
label="build/pro800" # what --dry-run prints for the default tool
PYTHON=${PYTHON:-python3}
dry=0
slots=""
port=""

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case $1 in
        --dry-run) dry=1 ;;
        --only) [ $# -ge 2 ] || { usage >&2; exit 2; }; slots=$2; shift ;;
        --tool) [ $# -ge 2 ] || { usage >&2; exit 2; }; tool=$2; label=$2; shift ;;
        --port) [ $# -ge 2 ] || { usage >&2; exit 2; }; port=$2; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "install.sh: unknown option $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

command -v "$PYTHON" >/dev/null 2>&1 || { echo "install.sh: $PYTHON not found (set PYTHON=)" >&2; exit 1; }

# The plan: every command, checked against the tool's names and ranges
plan=$(if [ -n "$slots" ]; then "$PYTHON" -I "$here/lib/plan.py" --slots "$slots" "$here"; else "$PYTHON" -I "$here/lib/plan.py" "$here"; fi)
[ -n "$plan" ] || { echo "install.sh: nothing to install" >&2; exit 1; }

have_seq=1
have_morph=1
if [ "$dry" -eq 0 ]; then
    [ -x "$tool" ] || { echo "install.sh: $tool not found; build it with scripts/tools.sh" >&2; exit 1; }
    help=$("$tool" help 2>&1 || true)
    case $help in *"seq set"*) ;; *) have_seq=0 ;; esac
    case $help in *"set morph"*|*"morph <slot"*) ;; *) have_morph=0 ;; esac
    "$tool" ${port:+--port "$port"} version >/dev/null || { echo "install.sh: the synth doesn't answer" >&2; exit 1; }
fi

warned_seq=0
warned_morph=0
printf '%s\n' "$plan" | while IFS= read -r line; do
    case $line in
        '#'*)
            printf '%s\n' "$line"
            continue ;;
        'seq '*)
            if [ "$dry" -eq 0 ] && [ "$have_seq" -eq 0 ]; then
                [ "$warned_seq" -eq 1 ] || echo "  (skipping the step sequence: this build/pro800 has no 'seq set' / 'seq step'; see presets/README.md)"
                warned_seq=1
                continue
            fi ;;
        'set morph '*)
            if [ "$dry" -eq 0 ] && [ "$have_morph" -eq 0 ]; then
                [ "$warned_morph" -eq 1 ] || echo "  (skipping the morph target: this build/pro800 has no 'set morph'; see presets/README.md)"
                warned_morph=1
                continue
            fi ;;
    esac
    if [ "$dry" -eq 1 ]; then
        printf '%s %s\n' "$label" "$line"
    else
        printf '  %s\n' "$line"
        # the line's arguments are shell-quoted by plan.py (patch names with spaces)
        if ! eval "\"\$tool\" \${port:+--port \"\$port\"} $line" >/dev/null; then
            echo "install.sh: failed: $tool $line" >&2
            exit 1
        fi
    fi
done

if [ "$dry" -eq 0 ]; then
    echo "done. The patches are in slots P1-P64 (program change 0-63)."
fi
