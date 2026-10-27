# Pro 800 firmware

Custom firmware for the Behringer PRO-800 (STM32F446). Not a Schwung module. Research, the pin map, the CV map
and the plan are in `NOTES.md`; read it before changing hardware code.

## Commands

- `scripts/build.sh` builds `build/pro800.elf` / `.bin` with zig cc and packs `build/pro800-<VERSION>.syx`
  with Music Tribe's `PRO800_update_gen`. `--no-pack` skips packing; `-h` prints the help.
- `scripts/tools.sh` builds `build/pro800`, the macOS USB MIDI tool (`build/pro800 help`):
  - Read-only: `ports`, `version`, `listen`, `backup <dir>` (system settings + patches as .syx).
  - `play <note> --record`: plays a note and measures level and pitch from the Move's audio input.
  - Changes the synth: `update-mode` (reboot into the bootloader) and `flash <file.syx> --yes`. Flash checks
    that the file is a packed update and paces it below the bootloader's 9.64 kB/s, waiting after the header
    for the flash erase. `--dry-run` only checks the file.
  - The synth listens on MIDI channel 6; `play` defaults to it.
- Flashing: `build/pro800 update-mode`, wait for USB ID `1397:1260`, then `build/pro800 flash
  build/pro800-<VERSION>.syx --yes`. Our firmware (from 0.0.8) handles the update-mode SysEx by erasing its
  own flash sector 4 and resetting; a build without working USB needs the buttons (0 and 7 at power-up).
- `VERSION` is the only version string; `scripts/build.sh` passes it in as `FW_VERSION_*` defines. It's x.y.z with single digits, because the bootloader takes it as XYZ.

## Layout

- `tools/pro800.swift`: the USB MIDI tool (one file, CoreMIDI + AVFoundation).
- `src/`: `startup.c` (vector table, reset handler that cleans up after the bootloader), `link.ld`
  (app at `0x08010000`, max 128 kB), `regs.h` (the registers we use; no vendor headers), drivers, `main.c`.
- `reference/`: GliGli's p600fw (GPL v3) and the packer + schematic. Third-party, git-ignored.
- `backup/`: read-only SysEx dumps of the synth's patches. Git-ignored.

## Rules

- Flashing the synth, or sending it anything other than read-only requests, only when the user asks.
- **Never** connect a debugger to unlock the flash: that erases the chip, bootloader included. Recovery is
  always through the bootloader (hold buttons 0 and 7 at power-up) and a stock firmware `.syx`.
- Don't touch the EEPROM (`EEP_*` on PE1–PE6) until we own the patch format; it holds the user's patches.
- At startup, drive every S&H enable (`EN1`–`EN9`) high before anything else, so no CV channel samples
  garbage.
- Porting p600fw code makes this firmware GPL v3.
- Realtime: no allocation or blocking work in interrupt handlers once the CV refresh runs from one.
