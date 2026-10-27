#!/bin/sh
# Builds the PRO-800 firmware and packs it into a SysEx update.
#
# Usage: scripts/build.sh [-h|--help] [--no-pack]
#
#   build/pro800.elf   linked firmware (app at 0x08010000)
#   build/pro800.bin   raw image, max 128 kB
#   build/pro800-<version>.syx   update file for the bootloader (skipped with --no-pack)
#
# The version comes from VERSION (x.y.z, single digits, as the bootloader takes it as XYZ).
# Packing uses Music Tribe's PRO800_update_gen from reference/PRO800-Firmware-Packer/.
# Environment: ZIG (default zig).
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
    [0-9].[0-9].[0-9]) ;;
    *) echo "VERSION must be x.y.z with single digits, got '$version'" >&2; exit 1 ;;
esac

major=${version%%.*}; rest=${version#*.}; minor=${rest%%.*}; patch=${rest#*.}

cd "$build"
"$ZIG" cc -target thumb-freestanding-eabihf -mcpu=cortex_m4+vfp4d16sp \
    -Os -std=c11 -Wall -Wextra -Werror -ffreestanding -nostdlib \
    -ffunction-sections -fdata-sections -ffile-prefix-map="$root"=. \
    -DFW_VERSION_MAJOR="$major" -DFW_VERSION_MINOR="$minor" -DFW_VERSION_PATCH="$patch" \
    -T "$root/src/link.ld" -Wl,--gc-sections -Wl,-e,Reset_Handler \
    -o pro800.elf "$root"/src/*.c
rm -f ./*.o
"$ZIG" objcopy -O binary pro800.elf pro800.bin

size=$(wc -c < pro800.bin | tr -d ' ')
[ "$size" -le 131072 ] || { echo "pro800.bin is $size bytes, over the 128 kB limit" >&2; exit 1; }
echo "pro800.bin: $size bytes"

[ "$pack" = 1 ] || exit 0

case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) packer_dir="macos arm64" ;;
    Linux-*) packer_dir=linux ;;
    *) echo "no PRO800_update_gen for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac
packer_src="$root/reference/PRO800-Firmware-Packer/$packer_dir/PRO800_update_gen"
[ -f "$packer_src" ] || { echo "packer missing: $packer_src" >&2; exit 1; }
cp "$packer_src" "$build/PRO800_update_gen"
chmod +x "$build/PRO800_update_gen"

syx="pro800-$version.syx"
"$build/PRO800_update_gen" pro800.bin "$syx" "$(echo "$version" | tr -d .)"
echo "$syx: $(wc -c < "$syx" | tr -d ' ') bytes"
