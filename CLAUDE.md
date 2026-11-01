# Pro 800 firmware

Custom firmware for the Behringer PRO-800 (STM32F446). Not a Schwung module. Research, the pin map, the CV map
and the plan are in `NOTES.md`; read it before changing hardware code.

## Commands

- `scripts/build.sh` builds `build/pro800.elf` / `.bin` with zig cc and packs `build/pro800-<VERSION>.syx`
  with Music Tribe's `PRO800_update_gen`. `--no-pack` skips packing; `-h` prints the help. On Windows, run it from
  Git Bash (`winget install zig.zig`). The packer salts its output, so two packs of one `.bin` differ: compare `.bin`s.
- `scripts/tools.sh` builds `build/pro800`, the macOS USB MIDI tool (`build/pro800 help`):
  - Read-only: `ports`, `version`, `listen`, `backup <dir>` (system settings + patches as .syx), `fault` (the last crash, SysEx 49, 0.9.4+).
  - `play <note> --record`: plays a note and measures level and pitch from the Move's audio input.
  - Changes the synth: `update-mode --yes` (reboot into the bootloader; it erases flash sector 4, so the synth stays there
    until flashed; without `--yes` nothing is sent) and `flash <file.syx> --yes`. Flash, `--dry-run` and `install` refuse a
    file that isn't a whole packed update: header only, under 10000 bytes, no `BEHRINGER PRO 800` and version in the header,
    a block count that differs from the header's size, blocks out of order or missing. Flash paces it below the bootloader's
    9.64 kB/s, waits after the header for the flash erase, then up to 7 s for `03 31`; stops at the first `01 00 xx` with xx
    nonzero; needs `01 00 00` after the last block (up to 6 s). Exit is nonzero on any failure, and the message says the way
    back. `--dry-run` only checks the file; `--require-ok` is accepted and does nothing.
  - `fault-test --yes` (0.9.4+) crashes the synth on purpose (SysEx 4A 43 52), waits up to 60 s for the restart and prints the
    record. It fails with no record, and refuses at streak 2 or more: the third crash in a row stops the synth until a power cycle.
  - Our firmware plays on every MIDI channel unless one is set (`set midi-channel all|1-16`, Shift + NOISE);
    `play`, `cc` and `bend` default to that channel, else 6 (the stock firmware's) for `play` and 1 for the others.
  - Knob pickup is on unless turned off (`set pickup on|off`, Shift + FIL ENV AMT, 0.9.3): off, a knob in pickup
    takes over as soon as it moves (the stock synth's jump).
  - `install <file.syx> --yes`: update-mode + flash + wait until the synth runs the file's version (it autotunes first). The bootloader's port has to stay 8 s first (`PORT_STEADY`), so pages left open finish probing it. Fails if the synth doesn't reach the bootloader (USB `1397:1260`), its MIDI port doesn't appear, the bootloader doesn't confirm the update, or another version answers; reinstalling the same version is fine.
  - Panel: `panel` (live control events), `state` (every pot and switch now), `leds` (raw display RAM).
  - Patches: `patches`, `patch load|save|clear <slot>`, `format --yes` (wipe every slot), `eeprom <file>` (read),
    `eeprom-write <image> --yes` (write and verify, e.g. back to stock).
  - Engine: `set lfo-sync|lfo2-sync free|clock`, `set lfo-trigger|lfo2-trigger free|first|every|voice|one-shot|one-shot-voice`, `set lfo-cycles|lfo2-cycles <1-99>` (a one shot's cycles, 0.9.3), `set lfo-restart steps|keys` (with a sequence on, what restarts the LFOs, 0.9.3), `route-both <layer> <source> <dest> on|off` (a route on both layers' voices, 0.9.3), `set layers
    off|split|stack|channel`, `set split <note>`, `set layer-voices auto|1-7`, `set keytrack`, `set lfo 1|2`, `set shape <layer> <lfo> <shape>`,
    `set lfo2-dests <layer> …`, `set env-up|dahdsr <layer> …`, `set env-curve <layer> <env> analog|linear|swell`,
    `set osc-tune <layer> <a|b> semitones|octave|free|fixed`, `set link none|amp-env,filter-env,env3,env4,lfo1,lfo2,filter` (what layer 2 takes from layer 1; `param` and the other `set`s write each layer's own stored values),
    `set env-delay|env-hold <layer> <env> <0-99>` (0.9.0), `set env-count <layer> <env> loop|1-99` (cycles), `set env-tempo <layer> none|amp,…` (stage times in note values), `settings`, `params`, `param <layer> <name> <0-99>`
    (M TUNE is `macro1` from 0.9.0), `layer 1|2`, `route`, `routes`,
    `clock <bpm> [seconds]`, `timing`.
  - Patches also: `patch rename <slot> <name>`, `factory` (list), `factory import <f> <slot> [--force]`.
  - From 0.8.0: 128 patches (P1-P128, program change 0-127), 768-byte records with the step sequence inside
    (`src/store.c`; `src/seq.c`, edited on the web page; `seq` prints it; from 0.9.3 `seq set chord=0-2` and `seq step … n2+4 n3+7 h1` for chords and held notes). 0.7.x records are wiped at the first start.
  - The WebMIDI page `scripts/web.sh` → http://localhost:8800 (Chrome/Edge; the desktop app's built-in browser
    has no MIDI): tabs Sound (the faceplate on top, then the extras and the mod matrix), Device cheat sheet (keep it in
    step with `MANUAL.md`), Patches, Play, System, Panel mapping (map in `tools/web/panel-map.json`).
  - Probe commands for our firmware: `direct on|off`, `cv`, `gate`, `measure` (the tuning comparator),
    `tune`, `tuning` (print the tables), `check` (cents error of every oscillator and, from 0.8.4, every filter through the tables),
    `dac-test` (sweeps the DAC80501 bus and the S&H waits, 0.7.4+; RAM-only probe, silent), `dac-heal` (sets the
    DAC80501's gain wrong and checks the firmware's setup re-send puts it right, 0.8.5+; silent). `engine` (held keys, voice gates and envelope
    stages, keytrack and switch overrides, from 0.9.3 the LFOs' phases; read-only).
- `tools/pro800.py` is the same tool for Windows, with the same commands: nothing to build, `python tools/pro800.py help`
  (Python 3.8+, WinMM through ctypes). Read `python tools/pro800.py` for `build/pro800` in these notes. Left out: `record`
  and `play --record`. On macOS and Linux it needs `pip install python-rtmidi`; on macOS the Swift tool stays the tested one.
  A new command goes in both tools. `python tools/test_pro800.py` runs it against a simulated synth (no MIDI port; it replaces
  `run_self` at import, so no test can start the tool on the real port): requests byte for byte, replies as `src/main.c`
  builds them, name tables and command list against `tools/pro800.swift`. The tool flashes
  like the Swift tool (same checks, pacing and erase wait), so quit SynthTribe and close WebMIDI pages first: under Windows
  MIDI Services they stay connected and every program sees every reply (NOTES). The stock firmware answers only `version`,
  `backup` and `listen`.
- `python tools/test_panel.py [-k name]`: the user-interaction tests, no synth needed, a few seconds. Builds the real firmware
  with zig cc for this computer (`tools/sim/hw.c` is the hardware, one UART interrupt a byte for the real `src/din.c`, which it
  includes as it does `controls.c`; `display.c`, `main.c`, `synth.c` and the rest run unchanged, `tools/sim/sim_regs.h` only
  redirects `REG()` and `NVIC_IPR`) and drives it through knobs, buttons, VALUE and MIDI, one millisecond a main-loop pass. Every
  test checks what the display or LEDs show; expected values come from `MANUAL.md`. A change to the panel or the display gets a
  test here. A power cycle is a new process on the same EEPROM file; system-wide settings are stored after a second's rest, so a
  test that power-cycles after one waits 1100 ms first. Simulator options: `--untuned`, `--no-crystal`, `--fault`, `--fault-old`,
  `--fault-bad`; commands only the tests use: `midistat` (USB events and DIN bytes waiting, discard state, autotunes with discard
  on and off), `writes` (EEPROM writes, and the count as the last autotune began), `eepromfail <0|1>`, `faultstore`. The simulator's
  update mode saves the EEPROM file. `UsbDriver` runs `tools/sim/usb_harness.c`, the real `src/usb.c` against a model of the OTG
  registers, built as `build/sim/usbharness` (USB itself stays stubbed in the simulator): a change to `usb.c` gets a test
  there. It checks the driver's register writes against the model, not the chip. `WebRanges` runs the editor's mod-range code in
  `node` against routes measured on the simulator's CVs; `WebInstaller`, `WebReconnect`, `WebRestore`, `WebSaveInASlot`,
  `WebMarkingTheMemory` and `WebSmallThings` run the pages' code in `node` against fake ports or the simulated firmware (all
  skipped without node), and `PresetBundle` checks `presets/lib/bundle.py`.
- Flashing: `build/pro800 install build/pro800-<VERSION>.syx --yes` (or `update-mode --yes`, then `flash ... --yes`). Our firmware (from 0.0.8) handles the update-mode SysEx by erasing its
  own flash sector 4 and resetting; a build without working USB needs the buttons (0 and 7 at power-up).
- The installer page `tools/web/install.html` (http://localhost:8800/install.html, on the site `editor/install.html`):
  WebMIDI, needs the same browsers. Flashes `firmware.syx` beside it (`scripts/web.sh` and `scripts/pages.sh` copy the
  current build there; git-ignored in `tools/web/`), initialises the memory, installs the preset bank, writes a memory
  image back, and goes back to stock from a file the user opens. **Behringer's firmware never goes on the site or in
  git.** `tools/web/sound-plan.js` holds what it shares with the editor: the preset-format code, `GLOBAL_ADDRESS` / `GLOBAL_MAGIC`,
  `nameBytes`, `badSlotPreset`, `formatReply` and `watchInstaller`, so a page must not redeclare them. Other MIDI software on the
  Mac breaks a flash (NOTES), the command-line flash too; the Chrome test uses a separate profile.
- The preset bank: `presets/lib/bundle.py` bundles `presets/*.json` into one file for the editor's Patches tab;
  `scripts/web.sh` writes `tools/web/presets.json` (git-ignored), `scripts/pages.sh` the site's `editor/presets.json`,
  and `scripts/build.sh` `build/pro8000-presets-<VERSION>.json`, which a release carries with the `.syx`.
- `scripts/pages.sh` builds the GitHub Pages site (`docs/` → `build/pages/`, with the editor and installer from `tools/web/` as
  `editor/`) and writes the `pages` branch as one parentless commit; `--push` force-pushes it. The site is
  https://klaine.github.io/pro-8000/ (repository `klaine/pro-8000`).
- `VERSION` is the only version string; `scripts/build.sh` passes it in as `FW_VERSION_*` defines. It's x.y.z with single digits, because the bootloader takes it as XYZ.
- `MANUAL.md` is the user manual (panel operation, the web page, recovery); keep it in step with panel changes.

## Layout

- `tools/pro800.swift`: the USB MIDI tool (one file, CoreMIDI + AVFoundation).
- `tools/pro800.py`: the same tool in Python for Windows (one file, WinMM through ctypes); `tools/test_pro800.py` tests it.
- `tools/test_panel.py`, `tools/sim/`: the firmware on the host with simulated hardware, and the tests that use its panel.
- `tools/sim/usb_harness.c`: the real `src/usb.c` against a model of the OTG core's registers, for the `UsbDriver` tests.
- `src/`: `startup.c` (vector table, reset handler that cleans up after the bootloader), `link.ld`
  (app at `0x08010000`, max 128 kB: flash sector 4 and half of sector 5), `regs.h` (the registers we use; no vendor headers), drivers, `main.c`,
  `fault.h` (the crash record that outlives a reset, and the handler's logic).
- `docs/`: the source of the two published guides (`index.html`, `how-it-works.html`, `sound-design.html`;
  `build.py` inlines `style.css` and `common.js`). They name the firmware version and describe its behaviour:
  keep them in step with `MANUAL.md` when a feature changes.
- `reference/`: GliGli's p600fw (GPL v3) and the packer + schematic. Third-party, git-ignored.
- `backup/`: read-only SysEx dumps of the synth's patches. Git-ignored.

## Rules

- Flashing the synth, or sending it anything other than read-only requests, only when the user asks.
- **Test on the init sound in the last slot (P128)**, unless a test needs something else (a particular patch, a
  layer mode, existing routes): a test that changes parameters, buttons, routes or per-patch settings must never
  touch the user's sound. Note the loaded patch (`pro800 settings`), then `pro800 patch init` and
  `pro800 patch save P128`, test there, and reload the user's patch afterwards. P128 is the test slot: overwrite it
  freely if it holds the init sound or a test, but ask first if it holds a patch the user made. Put system-wide
  settings you changed (MIDI channel, clock, CC A / B) back as they were.
- **Never** connect a debugger to unlock the flash: that erases the chip, bootloader included. Recovery is
  always through the bootloader (hold buttons 0 and 7 at power-up) and a stock firmware `.syx`.
- The firmware must fit in 128 kB (the packer's limit, enforced by `link.ld`): sector 4 and the first half of
  sector 5, which the bootloader erases and programs with each update (tested in 0.8.4). The tuning tables live in
  the EEPROM.
- The EEPROM (`EEP_*` on PE1–PE6) holds the user's patches in our format (`src/store.h`). Write it only through
  `store.c` or the tool's `--yes` commands; keep `backup/2026-10-27/eeprom-stock.bin` (the way back to stock).
- At startup, drive every S&H enable (`EN1`–`EN9`) high before anything else, so no CV channel samples
  garbage: `reset_body()` straight after the GPIO reset, before `.data` and `.bss`, and `main()` again first thing.
- Porting p600fw code makes this firmware GPL v3.
- Realtime: no allocation or blocking work in interrupt handlers once the CV refresh runs from one.
