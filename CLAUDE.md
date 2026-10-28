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
  - `install <file.syx> --yes`: update-mode + flash + wait until the new firmware answers (it autotunes first).
  - Panel: `panel` (live control events), `state` (every pot and switch now), `leds` (raw display RAM).
  - Patches: `patches`, `patch load|save|clear <slot>`, `format --yes` (wipe every slot), `eeprom <file>` (read),
    `eeprom-write <image> --yes` (write and verify, e.g. back to stock).
  - Engine: `set lfo-sync|lfo2-sync free|clock`, `set lfo-trigger|lfo2-trigger free|first|every|voice`, `set layers
    off|split|stack|channel`, `set split <note>`, `set keytrack`, `set lfo 1|2`, `set shape <layer> <lfo> <shape>`,
    `set lfo2-dests <layer> …`, `settings`, `params`, `param <layer> <name> <0-99>`, `layer 1|2`, `route`, `routes`,
    `clock <bpm> [seconds]`, `timing`.
  - Patches also: `patch rename <slot> <name>`, `factory` (list), `factory import <f> <slot> [--force]`.
  - From 0.8.0: 128 patches (P1-P128, program change 0-127), 768-byte records with the step sequence inside
    (`src/store.c`; `src/seq.c`, edited on the web page; `seq` prints it). 0.7.x records are wiped at the first start.
  - The WebMIDI page `scripts/web.sh` → http://localhost:8800 (Chrome/Edge; the desktop app's built-in browser
    has no MIDI): tabs Sound (the faceplate on top, then the extras and the mod matrix), Device cheat sheet (keep it in
    step with `MANUAL.md`), Patches, Play, System, Panel mapping (map in `tools/web/panel-map.json`).
  - Probe commands for our firmware: `direct on|off`, `cv`, `gate`, `measure` (the tuning comparator),
    `tune`, `tuning` (print the tables), `check` (cents error of every oscillator through the tables),
    `dac-test` (sweeps the DAC80501 bus and the S&H waits, 0.7.4+; RAM-only probe, silent).
- Flashing: `build/pro800 install build/pro800-<VERSION>.syx --yes` (or `update-mode`, then `flash`). Our firmware (from 0.0.8) handles the update-mode SysEx by erasing its
  own flash sector 4 and resetting; a build without working USB needs the buttons (0 and 7 at power-up).
- `VERSION` is the only version string; `scripts/build.sh` passes it in as `FW_VERSION_*` defines. It's x.y.z with single digits, because the bootloader takes it as XYZ.
- `MANUAL.md` is the user manual (panel operation, the web page, recovery); keep it in step with panel changes.

## Layout

- `tools/pro800.swift`: the USB MIDI tool (one file, CoreMIDI + AVFoundation).
- `src/`: `startup.c` (vector table, reset handler that cleans up after the bootloader), `link.ld`
  (app at `0x08010000`, max 64 kB: flash sector 4), `regs.h` (the registers we use; no vendor headers), drivers, `main.c`.
- `reference/`: GliGli's p600fw (GPL v3) and the packer + schematic. Third-party, git-ignored.
- `backup/`: read-only SysEx dumps of the synth's patches. Git-ignored.

## Rules

- Flashing the synth, or sending it anything other than read-only requests, only when the user asks.
- **Never** connect a debugger to unlock the flash: that erases the chip, bootloader included. Recovery is
  always through the bootloader (hold buttons 0 and 7 at power-up) and a stock firmware `.syx`.
- The firmware must fit in flash sector 4 (64 kB, enforced by `link.ld`): sector 5 holds the tuning tables.
- The EEPROM (`EEP_*` on PE1–PE6) holds the user's patches in our format (`src/store.h`). Write it only through
  `store.c` or the tool's `--yes` commands; keep `backup/2026-10-27/eeprom-stock.bin` (the way back to stock).
- At startup, drive every S&H enable (`EN1`–`EN9`) high before anything else, so no CV channel samples
  garbage.
- Porting p600fw code makes this firmware GPL v3.
- Realtime: no allocation or blocking work in interrupt handlers once the CV refresh runs from one.
