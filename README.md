# PRO-800 custom firmware

Alternative firmware for the Behringer PRO-800 (STM32F446), packed with Music Tribe's PRO800-Firmware-Packer and
flashed through the synth's own bootloader. Written from scratch for this hardware, with GliGli's p600fw (Prophet-600
firmware) as the reference for how the analog side behaves.

**Features**

- **Bitimbral:** two layers (split, stack, or by MIDI channel), selected with Seq 1 / Seq 2, with knob pickup.
- **LFOs:** two per layer, with eight shapes (triangle, square, ramps, sine, sample & hold, drift, and a 1-16 step
  pattern for trance gates, edited on the web page).
  - Sync to MIDI clock in 15 divisions.
  - Retrigger free, on the first key, on every key, or per voice (poly LFOs).
- **Envelopes:** four per voice, with analog-style curves and times shown in ms / s on the display. Env 3 and
  Env 4 are edited with the filter ADSR knobs. Any of them can cycle while a key is held (attack, hold, fall, rest).
- **Mod matrix:** 8 routes per layer, assigned from the panel (MOD ASSIGN on Arp Assign).
  - 11 sources: LFOs, envelopes, velocity, mod wheel, aftertouch, random, note.
  - 24 destinations, including the LFO rates and depths.
- **Autotune:** within about a cent across C1-C8. The tables are stored in the EEPROM.
- **Sequencer:** a step sequence per patch, played from a key and transposed by it (up to 64 steps, gate, ties,
  swing, clock sync), with up to four automation lanes as mod sources; edited on the web page.
- **Expression and play:** per-voice poly aftertouch, mono / legato / poly per layer with note priority, a sustain
  pedal, a MIDI CC for every knob, program change, analog-style drift, scale quantize, and patch morph (any mod
  source fades every knob towards another patch). The sequencer has probability, ratchets and an arpeggiator.
- **Patches:** 200 with names, each with its own sequence, and the stock factory bank kept and importable.
- **MIDI:** USB MIDI (also firmware updates without touching the panel) and the 5-pin MIDI IN socket for notes,
  controllers and clock.
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
build/pro800 install build/pro800-0.7.6.syx --yes
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
