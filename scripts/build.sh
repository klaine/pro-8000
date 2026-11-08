#!/bin/sh
# Builds the PRO-800 firmware and packs it into a SysEx update.
#
# Usage: scripts/build.sh [-h|--help] [--no-pack]
#
#   build/pro800.elf   linked firmware (app at 0x08010000)
#   build/pro800.bin   raw image, max 128 kB
#   build/pro800-<version>.syx   update file for the bootloader (skipped with --no-pack)
#   build/pro8000-presets-<version>.json   the 64-preset bank for the web editor (with the .syx, for a release)
#
# The version comes from VERSION: x.y.z, or x.1y.z (minor 10-19), which still fills the 4-digit display. The packer
# takes it as three characters, each one the number + '0' (0.10.0 is "0:0"); the header is read back to check it.
# Packing uses Music Tribe's PRO800_update_gen from reference/PRO800-Firmware-Packer/.
# Environment: ZIG (default zig), PYTHON (default python3, for the preset bank).
set -eu

case "${1:-}" in
    -h|--help) sed -n '2,/^set -eu/{/^set -eu/d;s/^# \{0,1\}//;p;}' "$0"; exit 0 ;;
esac
pack=1
[ "${1:-}" = "--no-pack" ] && pack=0

ZIG=${ZIG:-zig}
root=$(cd "$(dirname "$0")/.." && pwd)
build="$root/build"
mkdir -p "$build"

version=$(tr -d ' \n' < "$root/VERSION")
case "$version" in
    [0-9].[0-9].[0-9]|[0-9].1[0-9].[0-9]) ;;
    *) echo "VERSION must be x.y.z with single digits, or x.1y.z, got '$version'" >&2; exit 1 ;;
esac

major=${version%%.*}; rest=${version#*.}; minor=${rest%%.*}; patch=${rest#*.}

cd "$build"
"$ZIG" cc -target thumb-freestanding-eabihf -mcpu=cortex_m4+vfp4d16sp \
    -Os -std=c11 -Wall -Wextra -Werror -ffreestanding -nostdlib \
    -ffunction-sections -fdata-sections -fno-unwind-tables -fno-asynchronous-unwind-tables \
    -ffile-prefix-map="$root"=. \
    -DFW_VERSION_MAJOR="$major" -DFW_VERSION_MINOR="$minor" -DFW_VERSION_PATCH="$patch" \
    -T "$root/src/link.ld" -Wl,--gc-sections -Wl,-e,Reset_Handler \
    -o pro800.elf "$root"/src/*.c
rm -f ./*.o
"$ZIG" objcopy -O binary pro800.elf pro800.bin

size=$(wc -c < pro800.bin | tr -d ' ')
[ "$size" -le 131072 ] || { echo "pro800.bin is $size bytes, over the 128 kB limit" >&2; exit 1; }
echo "pro800.bin: $size bytes"
# The packer's 128 kB is a wall; past 110 kB there's little room left for the next releases (NOTES, the 0.10.0 plan)
[ "$size" -le 112640 ] || echo "warning: pro800.bin is over 110 kB ($size bytes)" >&2

[ "$pack" = 1 ] || exit 0

packer=PRO800_update_gen
packer_copy=$packer
case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) packer_dir="macos arm64" ;;
    Linux-*) packer_dir=linux ;;
    # Windows asks for elevation before running an unsigned 32-bit exe with "update" in its name, so the copy has another.
    MINGW*|MSYS*|CYGWIN*) packer_dir=windows; packer=PRO800_update_gen.exe; packer_copy=pro800-packer.exe ;;
    *) echo "no PRO800_update_gen for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac
packer_src="$root/reference/PRO800-Firmware-Packer/$packer_dir/$packer"
[ -f "$packer_src" ] || { echo "packer missing: $packer_src" >&2; exit 1; }
cp "$packer_src" "$build/$packer_copy"
chmod +x "$build/$packer_copy"

syx="pro800-$version.syx"
# One character per number, the number + '0': "095" for 0.9.5, "0:0" for 0.10.0
packer_char() { printf "\\$(printf '%03o' $((48 + $1)))"; }
"$build/$packer_copy" pro800.bin "$syx" "$(packer_char "$major")$(packer_char "$minor")$(packer_char "$patch")"
set -- $(od -An -tu1 -j42 -N3 "$syx")
[ "${1:-}.${2:-}.${3:-}" = "$version" ] || { echo "$syx: the header says ${1:-}.${2:-}.${3:-}, not $version" >&2; rm -f "$syx"; exit 1; }
echo "$syx: $(wc -c < "$syx" | tr -d ' ') bytes"
"${PYTHON:-python3}" -I "$root/presets/lib/bundle.py" "pro8000-presets-$version.json"
