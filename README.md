# PRO-800 custom firmware

Alternative firmware for the Behringer PRO-800 (STM32F446), packed with Music Tribe's PRO800-Firmware-Packer and
flashed through the synth's own bootloader. Written from scratch for this hardware, with GliGli's p600fw (Prophet-600
firmware) as the reference for how the analog side behaves.

**Features**

- **Bitimbral:** two layers (split, stack, or by MIDI channel), selected with Seq 1 / Seq 2, with knob pickup.
  Or one sound with a voice per MIDI channel: eight mono channels, or MPE with per-note bend, pressure and CC 74.
- **LFOs:** two per layer, with eight shapes (triangle, square, ramps, sine, sample & hold, drift, and a 1-16 step
  pattern for trance gates, edited on the web page).
  - Sync to MIDI clock in 15 divisions; a delay and fade-in per LFO, per voice.
  - Retrigger free, on the first key, on every key, or per voice (poly LFOs).
- **Envelopes:** four per voice, with analog-style curves and times shown in ms / s on the display. Env 3 and
  Env 4 are edited with the filter and amp ADSR knobs while Shift (Sync Source) is held. Any of them can cycle
  while a key is held (attack, hold, fall, rest).
- **Shift:** a second layer on the knobs (LFO delay and fade, drift, detune, bend range, macro offsets, CC
  numbers) and buttons (compare, undo, quick save, layer copy, settings pages, panic).
- **Mod matrix:** 16 routes per layer, assigned and browsed from the panel (MOD ASSIGN on Arp Assign), with two
  macros that are destinations and sources at once.
  - 28 sources: LFOs (either side of the middle, or 0 to 1), envelopes, velocity, mod wheel, aftertouch, pitch
    bend, pedal, two assignable CCs, CC 74 (timbre), gate, random, note, voice number, a fixed 1, the sequencer's
    four lanes and the macros.
  - 46 destinations: pitch, cutoff, the VCA, mixer and pulse widths, every envelope stage, glide, detune, volume,
    the LFOs' amounts, rates and routings, morph and the macros.
  - 24 destinations, including the LFO rates and depths.
- **Autotune:** within about a cent across C1-C8. The tables are stored in the EEPROM.
- **Sequencer:** a step sequence per patch, played from a key and transposed by it (up to 64 steps, gate, ties,
  swing, clock sync), with up to four automation lanes as mod sources; edited on the web page.
- **Expression and play:** per-voice poly aftertouch, mono / legato / poly per layer with note priority, a sustain
  pedal, a MIDI CC for every knob, program change, analog-style drift, scale quantize, and patch morph (any mod
  source fades every knob towards another patch). The sequencer has probability, ratchets and an arpeggiator.
- **Patches:** 128 with names, each with its own sequence, and the stock factory bank kept and importable. A
  64-sound bank for slots 1-64 is in `presets/` (`presets/install.sh`), built to show the engine off.
- **MIDI:** USB MIDI (also firmware updates without touching the panel) and the 5-pin MIDI IN socket for notes,
  controllers and clock. Or an internal clock with tap tempo, or the SYNC IN jack; the Sync Clock LED shows the beat.
- **A browser editor (WebMIDI):** sound, mod matrix, patches, factory bank, backup, tuning, plus a keyboard and a
  MIDI router.

Read **[MANUAL.md](MANUAL.md)** for how to use it, and [NOTES.md](NOTES.md) for the hardware research,
measurements and backlog.

## Build

Needs zig (as the C cross-compiler) and the Music Tribe packer in `reference/`, plus Xcode's swiftc for the tool.

```bash
scripts/build.sh
```

```bash
scripts/tools.sh
```

```bash
scripts/web.sh
```

`scripts/build.sh` writes `build/pro800-<VERSION>.syx`. `scripts/tools.sh` writes `build/pro800`, the macOS USB
MIDI tool. `scripts/web.sh` serves the editor at http://localhost:8800 (Chrome or Edge).

## Install

```bash
build/pro800 install build/pro800-0.8.2.syx --yes
```

Recovery is always possible through the bootloader: hold buttons 0 and 7 at power-up and flash any firmware `.syx`
(this one or the stock one, with SynthTribe). See the manual for going back to stock.

## Layout

| Path | Contents |
|---|---|
| `src/` | The firmware: drivers (DAC, S&H, panel, controls, EEPROM, USB MIDI, 5-pin MIDI in, tuning comparator), the engine (`synth.c`, `lfo.c`, `mod.h`), patch storage (`store.c`) and `main.c` (panel UI, SysEx). |
| `tools/pro800.swift` | The command-line tool (CoreMIDI). |
| `tools/web/` | The WebMIDI editor (one HTML file) and the panel map. |
| `reference/`, `backup/` | Third-party material and the synth's own backups, not in git. |
