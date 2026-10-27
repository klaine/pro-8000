# Pro 800 custom firmware: research notes

Goal: our own firmware for the Behringer PRO-800 with a **bitimbral mode** (Seq1/Seq2 pick the layer the
panel edits, internal sequencer dropped) and a **tempo-syncable, retriggerable LFO**.

`reference/` holds third-party material and is not ours to commit:
- `reference/PRO800-Firmware-Packer/`: Music Tribe's packer (`PRO800_update_gen`) and the main-board schematic.
- `reference/p600fw/`: GliGli's Prophet-600 firmware (GPL v3), which the PRO-800 is a descendant of.

**Licence:** if we copy or port p600fw code, our firmware is GPL v3 too, and we must publish its source.

## Platform (from the packer README)

- STM32F446VCT6, 8 MHz crystal, up to 180 MHz. The app's vector table and reset handler are at `0x08010000`.
  The bootloader at `0x08000000` is fixed and write-protected.
- Max firmware 128 kB, RAM 128 kB. Format is `.bin`, packed with
  `PRO800_update_gen fw.bin fw.syx <XYZ>`.
- Update mode: hold buttons 0 and 7 at power-up, or send `F0 00 20 32 00 01 24 00 03 30 F7`.
- Flash is read-protected. **Never** unlock it over SWD, because that erases the chip, bootloader included.
  So there's no SWD debugging of flash code. Debug over MIDI SysEx or USB instead.

## MCU pin map (sheet 1 of the schematic)

| Function | Pins / nets |
|---|---|
| CV DAC | DAC80501 (16-bit) over SPI: `DAC_MOSI` on PB0 |
| `HALF` / `FULL` | PB1 / PB2. Panel filter-slope LEDs or switches? To verify |
| S&H addressing | U28 (74HCT244 buffer) drives `SH0`–`SH2` and `EN4`–`EN8` from port B pins (PB5, PB9, PB10, PB12–PB15, wiring unclear); `EN1`–`EN3` on PD3–PD5, `EN9` on PD7 |
| Pot mux | `PM0`–`PM2` on PD0–PD2; 74HC4051 bank (sheet 4) → ADC; `CUTOFF_ADC` PC3, `NOISE_ADC` PA2 |
| Panel switches (direct GPIO) | `OSC1_TRI` PA8, `OSC1_SQ` PC4, `OSC2_SAW` PC5, `OSC2_TRI` PC6, `OSC2_SQ` PC7, `PW_A_B` PC8, `F_A_B` PC9, `FREQ_A` PC11, `LFO_SHAP` PC12, `LFO_FILTER` PC13, `FILTER` PC14, `UNISON` PC15, `OSC1_SAW` PE0 |
| Synth gates to voice board | `SAW_A` PE7, `TRI_A` PE8, `SYNC` PE10, `SAW_B` PE12, `TRI_B` PE13, `PMOD_FA` PE14, `PMOD_FIL` PE15 |
| Encoders / data | `VALUE_A_E` PE9, `VALUE_B_E` PE11 |
| Panel LEDs / buttons / display | HT16K33-style driver (`HT1_COM0-7`, `HT1_K1-13`) on I2C1 (PB6/PB7) |
| Autotune | `TUNE_SIG` PA6 (TIM3_CH1 input capture), `TUNE_CPR` PB3, `CAL_HOOK` from the voice board |
| Patch storage | SPI EEPROM: `EEP_SCK/MISO/MOSI/CS/HOLD/WP` on PE1–PE6 |
| MIDI | DIN in (opto), `MIDI_SEL`, `MIDI_THRU`; MIDI channel DIP `MIDI_CH_0..3` PD9–PD12 |
| USB | PA11/PA12 (USB FS), `USB_DEVICE_VBUS_IN` PA9 |
| Rear | `SYNC IN` jack via 74HC14 → PD14/PD15, `SYNC_SEL` PD8, sustain `BACKPANEL_PEDAL_SW`, expression `BACKPANEL_PEDAL`/`_DT` PA7, `BACKPANEL_PRESENCE` PD13 |

The pin table was read off a 300 dpi render and needs checking against the PDF before any code
relies on it.

## Control voltages

40 S&H channels: 32 per voice, 8 global (sheets 2 and 3, connectors X12/X13 to the voice board).

| Per voice (×8) | Global |
|---|---|
| `OSC_nA` pitch A, `OSC_nB` pitch B, `FIL_n` cutoff, `AMP_n` VCA | `PW_A_CV`, `PW_B_CV`, `RES_CV`, `VOL_A_CV`, `VOL_B_CV`, `NOISE_CV`, `M_VOL_CV`, `PMOD_OSC_B_CV`, `EXT_FIL_CV` |

Global switches: `SAW_A`, `TRI_A`, `SAW_B`, `TRI_B`, `SYNC`, `PMOD_FA`, `PMOD_FIL`.

This matches p600fw's `p600CV_t` / `p600Gate_t` (`common/synth.h`) almost exactly. The PRO-800 adds 2 voices
and `NOISE_CV`.

**Bitimbral split:** per layer we can vary pitch (tune, octave, detune, glide, bend, vibrato, poly-mod
fil-env → freq A, which p600fw computes in software), cutoff, filter env and keytrack, both ADSRs, layer level,
and LFO. Waveforms, PW, sync, mixer, noise, resonance and poly-mod osc-B amount/routing are shared.

Unknown until measured: the voice board isn't published, so we don't know the volts per DAC step or the cutoff
and VCA curves.

## p600fw architecture (what to port)

- `firmware/p600firmware.c`: AVR main loop plus a 2 kHz timer ISR → `synth_timerInterrupt()`.
- `common/synth.c` (1900 lines): the core. The ISR runs the LFO, computes the per-voice CVs (`refreshVoice()`)
  and splits slower work into 4 × 500 Hz phases: MIDI, clock/seq/arp, glide, envelopes, etc.
- `common/sh.c`: S&H refresh. `updateCV()` = DAC write → select demux address → wait → enable → wait → disable.
  The PRO-800 does the same with the DAC80501 and `SH0-2`/`EN1-9`. Timing needs re-tuning for the STM32.
- `common/dac.c`, `potmux.c`, `scanner.c`, `display.c`, `uart_6850.c`: P600 hardware drivers. Replace all of them.
- `common/tuner.c`: autotune. It opens one voice's VCA, does a successive-approximation search on the osc CV
  while measuring the audio period, for octaves C3..C6, and extrapolates the rest. The filter is tuned the
  same way (self-oscillating). The P600 measures with an 8253 counter and flip-flops; on the PRO-800,
  `TUNE_SIG` → TIM3 input capture makes this much simpler.
- `common/adsr.c`, `lfo.c`, `assigner.c`, `arp.c`, `midi.c`, `storage.c`, `ui.c`: mostly hardware independent,
  so porting them is realistic. `xnormidi/` is the MIDI parser.

## LFO in p600fw

- `lfo.c`: one global LFO (plus a separate vibrato LFO). Shapes: pulse, tri, rand, sine, noise, saw.
  The phase is a 24-bit accumulator with a half-period flip. `lfo_resetPhase()` exists.
- Patch parameter `spLFOSync` ("lfo trig"): `off`, `key` (reset phase when the first key goes down,
  `refreshModDelayLFORetrigger()`), or `1..8` (reset the phase every N clock steps, **only while the
  seq/arp runs**, in `synth_timerInterrupt` case 1).
- The rate is never tempo-locked. Only the phase is reset, so a free rate drifts against the clock.

What we'd add:
1. A tempo-locked rate: increment = f(clock period, division), from MIDI clock, the SYNC IN jack or internal BPM,
   whether or not anything else is running.
2. Retrigger modes: free / first key (legato) / every key / one-shot.
3. **Per-voice LFO phase**. The LFO is added into per-voice CVs in `refreshVoice()`, so each voice can carry its
   own phase. PW is global, so LFO → PW keeps a single phase.
4. One LFO per layer in bitimbral mode.

## Audio outputs

All 8 voices are summed to one mono signal (`PH_AUDIO`) on the voice board. Only that wire crosses X13.
On the main board it feeds AUDIO OUT (same signal on tip and ring), PHONES (U10) and `BACKPANEL_L/R`
(sheet 2). So layers **can't** go to separate outputs from firmware; that needs a voice-board mod
(break the sum, take voices 1–4 / 5–8 out separately), and the voice board isn't published.
What firmware can do: per-layer level via `AMP_n`, and muting a layer while editing.

## Stock firmware SysEx (community-documented)

Source: [samstaton/pro800](https://github.com/samstaton/pro800) (`pro800syx.md`, plus Defros's extended notes in
`defros/`). Reverse-engineered, not official. Header: `F0 00 20 32 00 01 24 00 <data> F7`.

| Request | Data | Reply |
|---|---|---|
| Firmware version | `08 00` | `09 00 xx yy zz` = version xx.yy.zz |
| System settings | `77 7E 03` | `78 7E 03 <packed data>` |
| Patch 0–399 | `77 lo hi` (7-bit, lo first; A00 = `00 00`, B00 = `64 00`, D99 = `0F 03`) | `78 lo hi <packed data>` |
| Unknown address | | `01 00 01` |

- Packed data: each group of 7 bytes is preceded by one byte holding their MSBs (bit 0 → byte 1).
- The patch layout follows p600fw's parameter order: format byte at offset 4 (`0x6F` from fw 1.3.6),
  continuous params from offset 5, 16-byte name at 150.
- There's no known dump for SEQ1/SEQ2. Manual per-voice retune values aren't kept across power-off (fw 1.4.6 bug).
- The stock firmware already has sync source settings (Internal / MIDI / USB / External, with PPQN and subdivision)
  for the arp and sequencer.
- Our USB tests (2026-10-27): notes on ch 6 play fine. The universal identity request gets no reply, but the
  version request does (`09 00 01 04 06` = **firmware 1.4.6**), so USB MIDI works in both directions.

## Feature ideas beyond the stock firmware

Everything here works through the per-voice pitch A / pitch B / cutoff / VCA CVs, which are pure software.
Some may already exist in a simpler form in stock firmware; check before building.

**Priority** (all share the per-voice modulation path, so each makes the next cheaper):
1. **MPE / poly aftertouch.** Per-note bend, pressure and slide → per-voice pitch, cutoff, level.
2. **Mod matrix.** Sources: 2–3 LFOs, a 3rd envelope, velocity, aftertouch, mod wheel, expression pedal,
   key position, random-per-note, voice number. Destinations: the per-voice CVs, plus the global ones
   (PW A/B, resonance, mixer, noise, poly-mod amount), which only take global sources.
3. **Mono mode on 8 MIDI channels.** 8 monosynths with their own pitch, filter and envelopes, sharing
   waveforms and resonance.

**Also worth doing:**
- Per-voice LFO phase and "analog drift" (slow random per-voice pitch and cutoff offsets).
- Envelopes: delay and hold stages, curves, looping (envelope as LFO), a 3rd mod envelope.
- Patch morph between two patches on the mod wheel or pedal (switches flip at the midpoint).
- Chord memory, strum, inversions and voicing spread.
- Voice modes: unison stacks (e.g. 2 notes × 4 voices), round-robin vs reuse, note priority,
  legato vs retrigger glide.
- Microtuning: port p600fw's MIDI Tuning Standard support, add Scala scales.
- Self-oscillating filter played as a tuned sine (the autotune already measures how the filter tracks pitch).
- Clock from the SYNC IN jack with divisions, and tap tempo. LFOs, arp and looping envelopes lock to it.
- Arp: ratchets, probability, Euclidean patterns, per-step cutoff / envelope / pitch changes.
- Full CC / NRPN for every parameter, with knob moves echoed to MIDI out.
- Knob pickup modes, assignable expression pedal, randomize / mutate patch.

**Not possible (fixed by hardware):**
- Per-voice waveform, PW, sync, resonance, mixer or noise. These are single shared wires.
- Separate audio outputs.
- Audio-rate digital modulation (FM) or effects. CV refresh runs at kHz rates and the MCU never sees
  the audio; the analog poly-mod path stays the only audio-rate modulation.

## Panel and safe state (checked on the schematic at 400 dpi)

- Display: 4-digit 7-segment (DS1, DS2, two digits each) on HT1 `COM0`–`COM3`; button LEDs and the POWER LED
  (DS14) on `COM4` and up. The HT16K33's address pins have no diodes, so it should be at 0x70
  (the test firmware scans 0x70–0x77). Membrane keys come in on `HT1_K1`–`K13` through 39k resistors.
- S&H wiring: `SH0` PB5, `SH1` PB9, `SH2` PB10; `EN1`–`EN3` PD3–PD5, `EN4`–`EN7` PB12–PB15, `EN8` PD6,
  `EN9` PD7 (B and PD6 through the U28 74HCT244, always enabled). `TUNE_CPR` reaches the MCU on PB8 via R32
  (R40 to PB3 isn't fitted).

## Test firmware 0.0.1 (built, not flashed)

`src/main.c`: resets everything the bootloader left running, switches to the 16 MHz HSI, drives all S&H enables
high, then blinks every panel LED and segment at 1 Hz. Holding a button keeps them lit (proves key scanning).
The independent watchdog is refreshed in case the bootloader started it. The voices aren't driven, so their
held CVs drift: turn the volume down while testing.

Stock tuning measured with `pro800 play <note> --record` (2026-10-27): C2 −3.3, C4 +2.5, C6 +7.9,
C7 +5.3 cents; the note sits around −28 dBFS against a −53 dBFS noise floor on the Move input.

Recovery: Music Tribe's packer README says the unit "can always be recovered via SynthTribe". SynthTribe 3.2.4's
`Devices/PRO-800/config.json` lists two USB product IDs, `0x125F` (the running firmware, as seen on our USB bus) and
`0x1260`, which is very likely the bootloader, so SynthTribe should find the synth in update mode too. The stock
firmware isn't bundled with SynthTribe; it comes from Behringer's cloud (`cloudfamily: pro800`) at update time.
Behringer doesn't publish a standalone 1.4.6 `.syx`, only the release notes.

Confirmed 2026-10-27: after `pro800 update-mode` the synth re-enumerates as `1397:1260` (serial number "0.1.0",
probably the bootloader version), and its MIDI port is still called "PRO 800". SynthTribe 3.2.4 finds the synth in
update mode, so the recovery path is in place.
**Update mode entered by SysEx survives a power-cycle**: the synth came back up as `1397:1260` again, showing its boot
text and an LED pattern, and stays there until a firmware is installed (SynthTribe or `pro800 flash`).

First flash (0.0.1, 2026-10-27, `pro800 flash` at 3000 B/s with a 3 s erase wait): the bootloader answered
`03 31` after the header and `01 00 00` after the last block (status OK, presumably; `01 00 01` is the stock
firmware's error). The synth then left the USB bus, as expected, since the test firmware doesn't run USB.
**It works**: all LEDs and segments blink at 1 Hz and holding a button keeps them lit. That confirms the whole
chain: zig build, linker address and vector table, the bootloader cleanup in `startup.c`, the HT16K33 at 0x70-0x77
over bit-banged I2C on PB6/PB7, and key scanning. Installing a firmware also clears the persistent update-mode
flag. To get back to stock: hold 0 and 7 at power-up, then update with SynthTribe.

Before the first flash we didn't know: how the bootloader answers (if at all), whether its USB MIDI port is still called
"PRO 800" in update mode, and how long it needs to erase after the header. `pro800 flash` waits 3 s after the
header (`--erase-wait`) and prints any reply.

## DAC and S&H wiring (sheets 1-3, checked at 400 dpi)

- DAC80501 (U46) in SPI mode (SPI2C tied low): SCLK PC10, SDIN PB0, SYNC PA15 (SPI3's pins; bit-banged for
  now). It runs from 3.3 V, so `GAIN = 0x0101` (REF-DIV on, buffer gain 2): 0–2.5 V out, doubled by U47A.
  Without REF-DIV the reference alarm forces 0 V.
- On-chip DAC1 (PA4) and DAC2 (PA5), through U16A/B.

| Mux | Enable | Source | Y0..Y7 |
|---|---|---|---|
| U25 | EN5 | DAC80501 | OSC_1A..OSC_8A |
| U32 | EN6 | DAC80501 | OSC_1B..OSC_8B |
| U43 | EN7 | DAC80501 | FIL_1..FIL_8 |
| U24 | EN8 | DAC2 | AMP_1..AMP_8 |
| U23 | EN9 (via U17B, non-inverting) | DAC1 | PMOD_OSC_B, VOL_A, VOL_B, M_VOL, NOISE, EXT_FIL, –, – |
| U15 | EN4 | DAC1 | PW_A, PW_B, RES, – |

Hold caps are 10 nF behind TL064 buffers. Pots: U22 (EN1 → VMUX1/PC0) amp and filter ADSR; U21 (EN2 → PC1) freq A/B,
fine, filter env amount, resonance, PW A/B, level A; U42 (EN3 → PC2) master tune, master volume, LFO freq/amount,
glide, level B, poly-mod osc B / filter env. Order within each pot mux not mapped yet. NOISE → PA2, CUTOFF → PC3.
The VALUE encoder is quadrature on PE9/PE11. Sheet 4 also has the button-LED map (COM4-7 × K5-7).

## Test firmware 0.0.2: first sound (2026-10-27)

Voice 1, saw A, pitch CV stepped 0x2000..0xF000 in 0x1000 steps; recorded with `pro800 record`.
Everything worked first time: DAC80501 setup, both on-chip DACs, the S&H mapping (OSC_1A, AMP_1, VOL_A, M_VOL,
PW_A) and the SAW_A gate. The VCA closes fully in the gap (−53 dBFS, the noise floor); the note sits at −38 dBFS
with master volume at 0x6000.

Oscillator 1A, untrimmed: **6515 codes per octave** (the 16-bit range covers 10.06 octaves), exponential within
±4 cents from 38 Hz to 3 kHz. 0x894D ≈ A440, 0x29DB ≈ C1. (The first recording showed 0xE000/0xF000 an octave low:
that was the pitch estimator's 4 kHz limit, since raised to 10 kHz.)

## Plan

1. Skeleton: arm-none-eabi-gcc, linker script at `0x08010000`, `SCB->VTOR`, clock tree 8 MHz → 180 MHz,
   build script → `PRO800_update_gen` → `.syx`. First test: light a panel LED or the display.
2. Before flashing anything: back up the patches (SynthTribe / SysEx) and keep the stock firmware `.syx` for
   recovery.
3. Drivers: DAC + S&H refresh, pot mux + ADC, panel switches, HT16K33, EEPROM, MIDI UART, USB MIDI.
4. Autotune with TIM3 input capture, then measure the cutoff, VCA and resonance curves.
5. Port the p600fw synth core (voices 6 → 8, add noise CV), then add the bitimbral mode and LFO sync.
6. Build the per-voice modulation path as a general mod matrix, then add MPE and the 8-channel mono mode
   on top of it.
