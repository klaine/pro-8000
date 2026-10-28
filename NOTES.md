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

## 168 MHz, hardware SPI, and the all-voices test (0.0.3–0.0.7, 2026-10-27)

- Clock: 8 MHz HSE → PLL (M 8, N 336, P 2, Q 7) = 168 MHz SYSCLK, 48 MHz for USB; APB1 42 MHz, APB2 84 MHz,
  5 flash wait states. Works (all timing measured correct in recordings).
- Pin functions confirmed from Zephyr's `stm32f446v(c-e)tx-pinctrl.dtsi`: SPI3_SCK PC10 AF6, SPI3_MOSI PB0 AF7,
  SPI3_NSS PA15 AF6, USB OTG FS DM/DP PA11/PA12 AF10.
- **Hardware SPI3 to the DAC80501 doesn't work** above APB1/128 (0.33 MHz): from 0.66 to 10.5 MHz the DAC gets
  wrong data (the voice plays a ~10 Hz sawtooth at −44 dB), while bit-banging at the same 168 MHz core clock
  works (0.0.5 A/B test, 0.0.6 speed scan). The first suspected cause, raising SYNC on BSY before the frame
  finished, was real and fixed (wait for RXNE per byte) but wasn't the whole story. Not a plain edge-rate limit,
  since 0.0.2's ~1 MHz bit-bang worked. Unresolved: probably data/clock timing at the DAC; needs a scope.
  `src/dac.c` bit-bangs at ~250 kHz for now (~50 µs per write, ~1.2 ms of each refresh).
- All-voices test (0.0.7): every channel maps as the schematic says. At CV 0x8000, untrimmed:

  | Voice | Osc A Hz | Osc B Hz | Filter (self-osc, full resonance) Hz |
  |---|---|---|---|
  | 1 | 341.9 | 333.8 | 1028.8 |
  | 2 | 340.2 | 335.9 | 1039.6 |
  | 3 | 335.5 | 340.5 | 1002.7 |
  | 4 | 350.2 | 330.5 | 998.6 |
  | 5 | 339.7 | 338.5 | 991.0 |
  | 6 | 332.4 | 345.4 | 1026.2 |
  | 7 | 351.7 | 324.5 | 983.2 |
  | 8 | 338.7 | 348.7 | 963.9 |

  Oscillators spread −77..+63 cents around 339 Hz (autotune will take that out); every voice within 0.7 dB at
  −38 dBFS (master 0x6000, mixer 0xC000); the filters self-oscillate at about −26 dBFS.

## USB MIDI (0.0.8, 2026-10-27)

- `src/usb.c`: OTG FS device in slave mode, interrupt-driven (IRQ 67), register-level (RM0390 ch. 30). No VBUS
  sensing: `GCCFG = PWRDWN`, B-session forced valid in `GOTGCTL`. FIFOs: 128 words RX, 64 for EP0 IN, 64 for
  the MIDI IN endpoint. USB MIDI 1.0 descriptors (one cable each way, bulk EP1 OUT/IN).
- It reports Behringer's IDs `1397:125F` and the product name "PRO 800" (manufacturer "AI & Klaine", serial =
  firmware version), so macOS keeps the "PRO 800" port name and `pro800` finds it with no options.
- Enumerated first time on macOS. Notes play on any channel (8 voices, saw A + B, untuned: within about
  −16 cents at C3–C5 on the voices tried). CoreMIDI needs a few seconds after re-enumeration before the port
  appears.
- SysEx: the version request (`08 00`) returns the firmware version; the stock update-mode command (`03 30`)
  mutes, detaches from USB, erases flash sector 4 from RAM (`src/update.c`) and resets. **Confirmed**: the
  bootloader then stays in update mode (`1397:1260` within 2 s) and `pro800 flash` reinstalls. The buttons are
  only needed if a firmware doesn't run USB.

## Autotune (0.1.0–0.1.5, 2026-10-27)

- Tuning path (sheet 3): `CAL_HOOK` from the voice board → buffer → LM311 U34A with hysteresis (10 MΩ, 22 pF),
  open collector pulled up by R111 → `TUNE_CPR` → PB8 (R32; R40 to PB3 not fitted). A second copy, buffered and
  diode-clamped, is `TUNE_SIG` on PA6 (an ADC pin; unused).
- `CAL_HOOK` carries the voice mix **after the per-voice VCAs and before the master VCA**: a closed VCA gives no
  signal, master volume 0 still does. So tuning is silent.
- Measuring (`src/tune.c`): each edge on PB8 is timestamped with the 168 MHz cycle counter in the EXTI9_5
  interrupt (highest priority) while the main loop keeps refreshing the S&H. The comparator is noisy at low
  frequencies: falling edges (the saw's reset) time single periods to about 1 cent, rising edges (slow ramp)
  jitter by several cents, and both sometimes add a stray edge mid-period or a burst of chatter, so first-to-last
  timing read 5/4 or 5/3 too high now and then. Fix: falling edges, median interval as the period, span between
  the first and last edges that border a median-length interval, periods = span / median rounded.
  Below about 30 Hz some oscillators get a stray edge every period and read an octave high.
- Oscillators drift up about 1 cent over a few seconds after a large CV change (thermal); not compensated.
- Tuner (`src/tuning.c`): each oscillator (saw on its own mixer channel) and filter (self-oscillating, both mixer
  channels off) is measured at CVs 0x2000–0xF000 (filters to 0xD000), **sweeping down from the top**: each point
  is expected 0.63 octave (filters 0.69) below the last good one, and readings more than a third of a step off
  are dropped. Notes interpolate linearly in log frequency between the nearest good points. About 33 s.
- Result: all 16 oscillators within **±0.7 cents from C1 to C8** (`pro800 check`, measured through `TUNE_CPR`);
  untuned they were up to ±120 cents off. Over MIDI the audio reads +2.3 cents, the average of osc A and osc B
  (detuned +5 cents on purpose).
- Storage: flash sector 5 (0x08020000), magic + checksum. **The bootloader erases sectors 4 and 5 on every
  update**, so the tables survive power cycles but not updates; the firmware tunes at boot when it finds none
  (panel lit, about 33 s, silent, USB starts afterwards). The SPI EEPROM is the only storage that would survive
  updates, but it holds the user's patches in an unknown layout.
- USB: our firmware now reports the chip-ID serial (ST's formula: UID0 + UID2, then the top of UID1), which is
  exactly the stock firmware's `203E30674E53`, so macOS treats it as the accessory it already approved.

## Panel map (0.1.6–0.1.7, 2026-10-27)

Mapped with the WebMIDI page (`scripts/web.sh`, `tools/web/`), saved as `tools/web/panel-map.json`; the
firmware's names are in `src/controls.h` and `src/display.c`.

- **Pots** (index = mux × 8 + address; each mux runs opposite to the schematic's drawing):
  - U22 / VMUX1: AMP REL, AMP SUS, AMP DEC, AMP ATT, FIL REL, FIL SUS, FIL DEC, FIL ATT
  - U21 / VMUX2: RESONANCE, FIL ENV AMT, FREQ A, FREQ B, FINE, A PW, B PW, A LEVEL
  - U42 / VMUX3: GLIDE, LFO FREQ, LFO AMOUNT, M TUNE, M VOL, B LEVEL, PMOD OSC B, PMOD FIL ENV
  - direct: CUTOFF (PC3), NOISE (PA2)
  - Each pot has 100 nF at the mux and each VMUX 100 nF at the MCU: the scan waits 4 ms per address. Idle
    readings stay inside a 24/4095 hysteresis.
- **Buttons** (HT16K33 key RAM byte.bit): Tune 0.0, 1 0.1, 2 0.2, 3 0.3, Preset 0.4, Perf 0.5, Settings 0.6,
  Arp Assign 0.7; Rec 2.0, 4 2.1, 5 2.2, 6 2.3, Sync Source 2.4, Sync Clock 2.5; Arp Up 4.0, 7 4.1, 8 4.2,
  9 4.3, 0 4.4, Seq 1 4.5, Seq 2 4.6.
- **Display**: digits left to right at RAM 2, 0, 6, 4. Each digit's address holds segments a, b, g (bits 0-2),
  and the next address f, e, d, c and the dot after the digit (bits 3-7). Everything else in RAM 0-7 is unused.
- **LEDs** (RAM.bit): Preset 8.7, Sync Source 9.0, Power 9.1, Perf 10.7, Seq 1 11.0, Seq 2 11.1, Settings 12.7,
  Sync Clock 13.0, Arp Up 13.1, Tune 14.7, Rec 15.0, Arp Assign 15.1. (Sheet 4's LED table has Seq 1/Seq 2
  swapped compared with the panel.)
- **Verified on the panel (0.1.7)**: digits (played note numbers, keypad typing), every button's LED while held,
  the power LED.
- **Switches** (GPIO, mapped with `pro800 panel` logs): Sync `SYNC_SEL` PD8; osc A Saw/Tri/Square
  `OSC1_SAW` PE0 / `OSC1_TRI` PA8 / `OSC1_SQ` PC4; osc B `OSC2_SAW` PC5 / `OSC2_TRI` PC6 / `OSC2_SQ` PC7;
  poly-mod → Freq A `FREQ_A` PC11, → Filter `FILTER` PC14; Unison PC15; LFO → Freq `F_A_B` PC9, → PW `PW_A_B`
  PC8, → Filter `LFO_FILTER` PC13; LFO Shape `LFO_SHAP` PC12. Latching buttons read 1 when latched down, and
  **down is off** on this panel (first read the other way round; corrected after playing 0.2.1).
  Keyboard tracking is a 3-position switch (Full / Half / Off, top to bottom) on two active-low lines: Off = both
  1, Half = `HALF` (PB1) 0, Full = `FULL` (PB2) 0.
- **VALUE encoder** (PE9 A, PE11 B via 74HC14): no detents; clockwise runs AB 11 → 01 → 00 → 10, four
  transitions per cycle, some contact bounce that cancels in the decode. (An earlier run seemed to show no
  encoder: that turn was the LFO FREQ knob, and the tools then misread the direction byte.)
- Not seen changing yet: `MIDI_CH_0..3` (PD9-PD12), `BACKPANEL_PRESENCE` (PD13), `PEDAL_DT` (PA7), SYNC IN
  (PD14/PD15).

## Synth engine (0.2.0–0.2.1, 2026-10-27)

`src/synth.c` replaces the test voice code: the front panel sets the sound, MIDI (any channel) plays it, pitch
bend ±2, mod wheel adds LFO depth. Everything runs in the main loop with the real elapsed time (about 2.2 ms per
pass, set by the S&H refresh). Pitch, filter cutoff and keyboard tracking are worked out in semitones and turned
into CVs through each voice's tuning tables. First-guess ranges, all constants in `synth.c`:
- frequency knobs ±24 semitones in semitone steps (±36 from 0.6.1); fine (B) ±1; master tune ±1
- envelopes 1 ms – 10 s per stage (linear attack, exponential decay and release); filter envelope amount bipolar,
  ±72 semitones; cutoff knob 16 Hz – 16 kHz; keyboard tracking 0 / ½ / 1
- LFO 0.05–50 Hz, triangle (or square with LFO Shape), depth = amount² (+ mod wheel), to pitch ±12 semitones,
  filter ±48, PW ±0.45
- poly-mod: filter envelope to oscillator A's pitch in software (up to 48 semitones); oscillator B through the
  analog path with PMOD OSC B as its amount
- unison: all 8 voices, last-note priority, legato, 2-cent spread; glide 2 ms – 2 s time constant
- pulse width: square off = PW A at maximum, PW B at minimum (as p600fw); on = 5–95 % of the CV range
- display shows a pot's value (0-99) while it moves; Tune runs autotune; `pro800 state` dumps the panel
- Played by ear on 0.2.2: the basic patch sounds right and every button does what the panel says.

## LFO sync and retrigger (0.2.3, 2026-10-27)

- `src/lfo.c`: free (0.05–51 Hz) or locked to MIDI clock (USB realtime: F8 clock, FA start, FC stop). The
  clock interval is averaged; a tick position advances continuously between ticks, snaps to the count on each
  tick and isn't allowed more than a tick ahead while the clock runs, so the LFO is smooth and on the grid.
  Start resets the bar; if the clock stops it carries on at the last tempo. In clock mode LFO FREQ picks one of
  15 divisions (8 bars … 1/32, dotted and triplet), shown on the display while turning.
- Retrigger: free / first key (`1St`) / every key (`EACH`) / per voice (`PoLY`, a phase per voice for pitch and
  filter; PW has one CV so it stays on the shared LFO). With the clock and free retrigger the phase follows the
  grid; with a retrigger the rate follows the clock and the phase starts at the key.
- Settings menu: Settings (LED on, `SEt`), key 1 = Sync (`FrEE`/`CLoC`), key 2 = retrigger, VALUE knob changes
  it. In RAM only for now. From the Mac: `pro800 set lfo-sync|lfo-trigger …`, `pro800 clock <bpm>`.
- Measured: square LFO on pitch, 1/4T at 120 BPM from `pro800 clock`: 27 flips averaged 0.1667 s apart (expected
  0.1667), every gap 0.1646–0.1696 s (5 ms analysis steps). Heard on the speakers too. Tried by hand: the
  menu, all four retrigger modes, and clock (with start) from the Ableton Move through the web router. Note: the audio pitch
  check needs a plain patch: hard sync, or oscillators tuned apart, defeat it.

## Bitimbral layers (0.3.0–0.3.1, 2026-10-27)

- Two layers of the per-voice knobs (FREQ A/B, FINE, CUTOFF, FIL ENV AMT, both ADSRs, GLIDE, LFO FREQ/AMOUNT,
  PMOD FIL ENV, M TUNE as layer fine-tune) and an LFO each (shared clock). Shared, because the hardware has one
  set of wires (or a button shows one state): mixer, resonance, PW A/B, PMOD OSC B and the routing, M VOL, every
  button (keyboard tracking and unison included), LFO → PW (from layer 1's LFO).
- Modes (Settings key 3, `LAYr`): OFF (layer 1 on all 8 voices), SPLt (layer 1 below the split note, layer 2
  from it up), StAC (both layers on every note), CHAn (MIDI channel 2 → layer 2, others → layer 1). Layers 1/2
  use voices 1-4 / 5-8 except with OFF. Split note: Settings key 4, VALUE knob or play a note.
- Seq 1 / Seq 2 pick the edit layer (LED steady, display `L  1` / `L  2`). A per-layer knob then waits for
  pickup (within 1.5 % or swept past the stored value); until then the display shows the stored value with a dot.
- Bug found by measuring: layer 2 was silent because both layers were primed from the pots before the first full
  scan had read them (all 0). Fixed with `controls_ready()`. Measured after: split plays both halves at the same
  level; stack is 1–4.4 dB louder (two voices beating).
- From the Mac: `pro800 set layers off|split|stack|channel`, `pro800 set split <note>`, `pro800 layer 1|2`.

## Patch EEPROM (0.3.2, 2026-10-27; read-only)

U11 is a 25M01 (1 Mbit, 128 kB SPI EEPROM, 24-bit addresses) on PE1 WP, PE2 SCK, PE3 HOLD, PE4 CS, PE5 SO,
PE6 SI (`src/eeprom.c`, bit-banged mode 0, READ and RDSR only, WP held low). Status register 0x00. Full image:
`backup/2026-10-27/eeprom-stock.bin` (SHA-256 1bff1f71…2e5d; `pro800 eeprom <file>` reads it in 10 s).
Stock layout, matched against the SysEx patch dump:
- 0x00000–0x18FFF: 400 user patches (A00–D99), 256 bytes each: the unpacked SysEx patch body (starts
  `a5 16 61 00 <format 6D/6F>`), zero-padded; an empty slot is all zeros. All 112 used slots match exactly.
- 0x19000–0x1F3FF: 100 factory patches (factory-reset copy: "Organ I", "Classical Brass", …).
- 0x1F400–0x1F6FF: small records (12 bytes at 0x1F400; patch-shaped records at 0x1F500 "PL Synthwave 16" and
  0x1F600): probably edit / copy buffers. 0x1F900–0x1FC70: ~840 bytes of dense data (stock tuning calibration or
  SEQ1/SEQ2?). 0x1FD00: patch-shaped ("Brass I"). 0x1FE00: system settings (the 40-byte SysEx system body).
- zeros: 0x1F700–0x1F8FF, 0x1FF00–0x1FFFF.

## Patch storage (0.4.0–0.4.1, 2026-10-27)

Our firmware owns the EEPROM (decided 2026-10-27); `backup/2026-10-27/eeprom-stock.bin` + `pro800 eeprom-write
<image> --yes` is the way back to stock (writes and verifies all 128 kB). Layout (`src/store.h`):
- 0x00000–0x18FFF: 400 patch slots (A00–D99), same slots as stock, 256 bytes in our format `P8P1`: name, layer
  mode, split, LFO sync and retrigger, latching buttons, keyboard tracking, both layers' 26 knob values (u16),
  reserved space for mod routes, checksum.
- 0x19000–0x1F3FF: the stock factory bank, untouched (importable later).
- 0x1F400: global block `P8G1` (last patch, preset/manual). 0x1F500: the autotune tables, which now survive
  firmware updates (no more retune after `install`).
- Import (`pro800 import --yes`): converted all 112 stock patches in place, names kept, 0 errors. The stock fields
  map one to one (u16 little-endian; LFO shape pulse → square; LFO targets bit 0 freq, 1 filter, 2 PW), but the
  engine's curves differ, so imported sounds are close, not identical. M TUNE becomes centre; M VOL isn't stored.
- Panel: PRESET toggles preset (patch loaded: knobs in pickup, buttons hold the stored state until toggled) and
  manual (panel live); two keypad digits pick a patch in the bank, VALUE steps through all 400 (display ` A.07`);
  REC (blinks), choose the slot, REC saves (`donE`); the loaded patch's name follows it to an empty slot. Boot
  restores the last patch and mode.
- PERF loads an init sound (0.4.2): osc A saw at concert pitch, filter open, gate-like amp envelope, everything
  else off, name "Init"; REC saves it anywhere. Measured over MIDI from the audio: −29 dBFS, C3/C4/C5 at
  −0.3/−0.4/+0.5 cents (the cleanest end-to-end check of the tuning so far). `pro800 patch init` does the same.
- Checked: load + play A00/A01/A02/B04; save A01 to an empty slot and compare records: identical apart from the
  low byte of a few knob values (1/65536 rounding) and M VOL (not applied); test slots cleared again.

## Env 3 / Env 4 and the mod matrix (0.5.0, 2026-10-27)

- Each voice has two more ADSRs. The keyboard-tracking switch now picks what the filter ADSR knobs edit: bottom =
  filter envelope, middle = Env 3, top = Env 4 (display `FEnV` / `EnV3` / `EnV4`, pickup on switching). Keyboard
  tracking moved to Settings key 5 (`trAC`: OFF / HALF / FULL), stored per patch.
- Mod matrix (`src/mod.h`), 8 routes per layer, stored in the patch (bytes 136–215; older patches read as no
  routes). Sources (keypad in MOD ASSIGN): 1 LFO, 2 filter env, 3 amp env, 4 velocity, 5 mod wheel, 6 aftertouch
  (channel and poly pressure), 7 Env 3, 8 Env 4, 9 random per note, 0 note position. Destinations (touch the
  control): FREQ A → pitch A, FREQ B / FINE → pitch B, M TUNE → both, CUTOFF, FIL ENV AMT, PMOD FIL ENV, LFO AMOUNT,
  AMP SUS → VCA level, the LFO → Freq / Filter / PW buttons → that LFO depth; single wires (taken from the layer's
  most recently played voice): A/B LEVEL, NOISE, RESONANCE, A/B PW, PMOD OSC B. Depth 99 = ±24 semitones of pitch,
  ±96 of cutoff, full scale of levels and depths, ±0.5 of PW.
- MOD ASSIGN on Arp Assign (LED blinks): keypad = source (`LFO`, `FEnV`, `AEnV`, `VEL`, `nod`, `PrES`, `EnV3`,
  `EnV4`, `rAnd`, `notE`), touch a destination (`P.A`, `P.b`, `PIt`, `CUt`, `F.EA`, `P.EA`, `L.An`, `L.Fr`,
  `L.FL`, `VCA`, `A.LE`, `b.LE`, `noIS`, `rES`, `A.PU`, `b.PU`, `P.ob`, `L.PU`), VALUE sets the depth for the
  edit layer. Knobs go into pickup while assigning; a toggled LFO button keeps its routing.
- Measured (init sound, `pro800 route` / `cc`): mod wheel → pitch A at depth 50: wheel 64 → +610.6 cents
  (expected 610.9), 127 → +1211.4 (1212.1); velocity → pitch A at 99: velocity 64 → +1208.9 (1209.4),
  127 → +2400.6 (2400).
- Timing (`pro800 timing`): engine 132 µs, CV refresh 2146 µs, a main-loop pass 4151 µs. Flash 28.5 kB of the
  64 kB link limit (the packer allows 128 kB; flash sector 5 is free now the tables live in the EEPROM), RAM about
  5.6 kB static of 128 kB.

## Main-loop timing (0.5.1–0.5.6, 2026-10-27)

- `pro800 timing` (SysEx 58) reports the engine, CV refresh, a main-loop pass, the longest pass, the panel poll,
  the display update and passes per second.
- The stray ~1.9 ms was the HT16K33 key scan: reading all six key bytes over the bit-banged I2C every 40 ms. Now
  each scan reads only the key-interrupt flag (one byte); the key RAM is read when the flag says a key is down, while
  any key is held, and on every 8th scan as a fallback (so a missed flag costs at most 320 ms).
- Faster DAC clock tried and reverted: bit-banging at 1 MHz garbles the DAC80501 just like hardware SPI did above
  0.33 MHz, so it stays at about 500 kHz (`HALF_BIT_CYCLES` 168). The limit looks like the board, not the code.
- 0.5.6: engine 131 µs, CV refresh 2122 µs, average pass 2331 µs (429 updates per second), longest pass
  3.2–4.1 ms. Tuning worst error 0.9 cents.

## LFO 2, LFO shapes, host editing (0.6.0, 2026-10-27; built, not yet flashed)

- Seven LFO shapes, chosen per LFO and per layer: triangle, square (`PULS`), ramp up (`SA.UP`), ramp down
  (`SA.dn`), sine (`SInE`), sample & hold (`S-H`, a new random value every cycle) and drift (`drFt`, smooth
  glides between random values). Every toggle of the LFO Shape button steps to the next shape; for 2 s after it
  VALUE steps through them both ways. The button's own position no longer means anything. Old patches: LFO 1
  square if the shape bit was set, else triangle.
- LFO 2 per layer: Arp Up-Dn (LED = LFO 2) switches what the LFO FREQ / AMOUNT knobs (with pickup), the shape
  button, the LFO routing buttons and the Settings SYnC / trIG pages (shown as `SYn2` / `trI2`) edit. LFO 2's
  routing buttons are virtual: toggling one with LFO 2 selected flips LFO 2's routing (display `Fr.on`, `FL.oF`,
  `PU.on`…) and holds LFO 1's (the button's position keeps showing LFO 1's). Sync and retrigger are per LFO
  (LFO 1 / LFO 2), shared by both layers. LFO 2 has no mod wheel depth; LFO → PW uses layer 1's LFOs.
- Mod matrix: source LFO 2 (key 1 in MOD ASSIGN picks the selected LFO, pressing it again swaps); destinations
  LFO 1 rate, LFO 2 amount, LFO 2 rate, LFO 2 → pitch / filter / PW (touch the LFO knobs or buttons with LFO 2
  selected). Rate depth 99 = ±4 octaves; in per-voice (`PoLY`) retrigger the rate is modulated per voice (e.g.
  velocity → vibrato speed per note), otherwise from the most recently played voice.
- `synth_hold_switch()` now keeps a patch's held state when a button is toggled during MOD ASSIGN (it used to
  take the inverse of the button's position).
- Patch format: LFO 2 sync and retrigger at 216, shapes + 1 at 218 (0 = not stored), LFO 2 routing at 222, LFO 2
  rate and amount at 224; all zero in older patches, which means what older firmware did.
- Host commands: settings readback (54) and more settable settings (7D: LFO 2 sync / retrigger, edit layer,
  selected LFO, shapes, LFO 2 routing), parameter readback / set (53 / 52, every knob, Env 3 / 4, LFO 2), patch
  rename (59), clear (4F), factory bank names (50) and import into a slot (51; the factory bank holds 100 stock
  patches, "Organ I" … "Alien"). The tool has `settings`, `params`, `param`, `set shape|lfo2-dests|lfo|keytrack`,
  `patch rename`, `factory`.
- Web page reorganised into tabs: Sound (every parameter of the edit layer, LFO shapes, sync, retrigger, LFO 2
  routing, layers; follows the panel every 0.9 s), Mod matrix, Patches (load, save, rename, clear, export / import
  .p8p, back up all 400, the factory bank), Play, System (timing, autotune, tables) and Panel mapping.
- Checked on the Mac with the real `lfo.c`: every shape spans −1..1 with the right form (sine within 0.12 % of
  −cos), +1 octave of rate halves the period, per-voice phases advance on their own. Build 33.5 kB, `-Werror`
  clean. Not flashed yet (needs the user's go-ahead).

- 0.6.1: FREQ A / B ±36 semitones in semitone steps (was ±24; ±48 tried first, too fiddly); FINE stays ±100 cents. While turning, the display
  shows semitones (`-12`, ` 7`) or cents (`-100`) instead of 0-99. Stored patches keep their knob positions, so a
  patch saved with an interval comes back with 1.5 times that interval: re-set FREQ A / B on older patches.

## Envelope ranges, curves and cycling (0.6.2, 2026-10-27; built, not flashed)

- Ranges came from what the engine can resolve: it updates every ~2.3 ms, so the old 1 ms minimum wasted the bottom
  ~12 % of each time knob (1-2.5 ms all sound the same), and the old decay/release "time" was to −34 dB, so tails
  lasted about twice what the number suggested.
- Every time knob now follows t = min + (max − min) · (2^(12k) − 1) / 4095: no dead zone at the bottom, the middle
  where most sounds live. Attack 0-10 s (17 ms at ¼, 150 ms at ½, 1.25 s at ¾); decay and release 3 ms-20 s
  (310 ms at ½) to −60 dB. The longest release tail is about what it was (old max: −34 dB at 10 s).
- Attack charges towards 1.5 and stops at 1 (the CEM3310 / SSM2056 shape, as p600fw's "exp" attack): 0.39 of the
  way at a quarter of the time instead of 0.25. Decay and release exponential. Sustain stays linear: this assumes
  the voice VCA is linear in amplitude (p600fw drives it straight from its envelope). Not measured yet (the probe
  run was blocked); if the VCA turns out exponential, sustain and the release shape need a curve.
- Per-update factors are worked out once per layer and envelope (8 × 3 exp2) instead of per voice.
- Cycling (per envelope and layer, Settings key 6 `CY.A` / `CY.F` / `CY.3` / `CY.4`, patch byte 232): attack,
  hold (SUS knob as a time), fall to −60 dB (DEC), rest (REL), again while the key is held; key up falls with the
  DEC time. Host setting ids 15-16; `pro800 set cycle <layer> amp,filter,env3,env4`; a checkbox per envelope on the
  web page, which also relabels the knobs.
- The display shows envelope times while turning: ` 150` (ms), ` 1.25` / ` 12.5` (s).
- Checked on the Mac with the real `synth.c` (hardware stubbed, 2.33 ms updates): the attack peaks at 0.151 s for
  0.154; release reaches −60 dB at 0.308 s for 0.311; a cycle of 17 + 154 + 311 + 17 ms repeats every 0.503 s.
- Older patches: the same knob positions now give somewhat longer decays (0.5: 310 ms to −60 dB, was 100 ms to
  −34 dB, about 180 ms to −60 dB) and slightly shorter attacks in the lower half.

## Step LFO shape and the VCA as a gain (0.6.3, 2026-10-27; built, not flashed)

- Shape 8, `StEP`: a pattern of 1-16 on/off steps per LFO per layer (bit n = step n), played over one cycle, +1 / −1,
  with an 8 ms slope at each change (3 ms was tried: the VCA opens over only the upper half of the swing, so it
  opened within one 2.3 ms update; 8 ms spreads it over about two). Default `1 0 1 0 1 1 0 0` twice (the user's
  "ta-ta-taa"). Patch bytes 234 (patterns, u16) and 242 (counts; 0 = not stored → default). SysEx 4E sets, 4D
  reads; `pro800 set steps <layer> <lfo> x.x.xx..`; edited only on the web page (a step grid under the LFO when its
  shape is Steps, with count, clear, invert, rotate, trance gate and "Route to VCA").
- The VCA destination was additive (envelope + mod), so an LFO → VCA route opened idle voices: they would sound with
  no note playing. Now it's a gain on the amp envelope, clamp(1 + mod, 0, 1), and 0..1 sources count down from full
  (source − 1), so velocity → VCA at 99 is velocity sensitivity and a bipolar source at 99 gates fully.
- Checked on the Mac (real `synth.c` and `lfo.c`, hardware stubbed): LFO 2 on `StEP` at 1 Hz routed to the VCA at
  99 plays x.x.xx..x.x.xx.. sampled mid-step; no VCA opens before the note or after the release; velocity 32 →
  0.252 gain (32/127).

## VCA response (measured on 0.6.3, 2026-10-27)

- Direct mode, saw A into an open filter, one voice's AMP CV stepped, level from the Move input (noise floor
  −52.5 dBFS). Linear in amplitude above a dead zone: amplitude = (CV − c) / (1 − c) with c = 0.089 on voice 1
  (fit from −37 dB to full; −6.7 dB at half CV). Voices 2-8 at CV 0.125: c = 0.073-0.090; at half CV all within
  0.3 dB of each other.
- Consequence: every envelope tail and low sustain stopped at about −20 dB (a release went silent at about a third
  of its time). 0.6.4 maps the amp envelope onto the VCA from 0.07 (just under the lowest voice) to full, and gives
  an idle voice 0. Sustain stays linear in amplitude (50 = −6 dB).
- Tuning check on 0.6.3: worst 1.1 cents (the top octave runs up to +1 cent sharp as the synth warms up).

## Pitch route depth curve (0.6.5, 2026-10-27)

- Pitch destinations (pitch A, pitch B, both) scale the route depth squared: semitones = 24 · sign(d) · (d/99)².
  Linear, depth 1 was already 0.24 semitones, too coarse for vibrato. Checked through `synth.c` on the Mac: depth 5
  → 0.061, 10 → 0.245, 20 → 0.979, 50 → 6.12, 99 → 24.0, −10 → −0.245 semitones. Existing pitch routes come out
  shallower (except at 0 and ±99).
- The web editor got depth sliders per LFO (VCA, cutoff, pitch A / B, PWM = the A and B pulse-width routes), backed
  by mod routes on the edit layer; pitch readouts use the curve (cents below a semitone).

## Synced cycling, buttons from the host (0.6.6, 2026-10-27; built)

- A cycling envelope can take a clock division (Settings key 6: `OFF`, `FrEE`, `8bAr` … `1-32`; host settings 17-24;
  patch bytes 246-249, a nibble per envelope). Its period is the division at the LFO clock's tempo (`lfo_quarter_
  seconds()`, 120 BPM before any clock) and the four knobs are weights sharing it out (attack, hold, fall, rest).
  A phase drives the level (attack curve, hold, −60 dB fall, rest), restarting at each key, so it can't drift:
  on the Mac, 80 cycles of 1/16 at 120 BPM put the 80th peak 0.9 ms from the grid (under one update). While turning
  a knob the display shows its share in %.
- Host commands 4C (read the latching buttons as the engine uses them) and 4B (set one; it holds like a loaded
  patch's state until the button is toggled). `synth_switch()` / `synth_set_switch()`. Tool: `set button <name>
  on|off`, buttons in `settings`.
- Web editor: oscillator cards carry their wave and sync buttons next to the level faders (the Mixer card became
  Noise), poly-mod, LFO 1 routing and unison buttons sit in their cards; envelopes have a Mode menu (ADSR, cycle,
  synced cycle at a division) and show shares while synced.

## Rate modulation on a grid-locked LFO (0.6.7, 2026-10-28)

- Before, a clock-synced LFO with free retrigger took its phase straight from the clock position, so LFO rate
  routes did nothing. Now, while the rate modulation is over 0.01 octave, the phase runs freely at the modulated
  rate (smooth sweeps, off the grid); below it, the LFO runs up to 2 × its phase error faster or slower until it's
  within 0.002 of a cycle, then follows the grid again. Checked on the Mac with a simulated 120 BPM clock: 1/8
  swept ±2 octaves by a 2 s triangle gives cycles of 0.065 s (1/32: 0.0625) up to 0.53 s; released 0.23 of a cycle
  off the grid, back on it within a second. The user wanted smooth sweeps, not octave steps.

## Sequencer with automation lanes (0.7.0, 2026-10-28)

- `src/seq.c`: a step sequence per patch, played from a key (the user's choice: "play on key down, transposed from
  the note"). Up to 64 steps (note offset + 64, velocity or the key's, gate % with 100 = tie, on/rest), step = an
  LFO clock division at the MIDI clock tempo or its own BPM, swing, loop or once per key, keys of both layers or one
  (notes go to the trigger key's layers: `synth_key_layers()` / `synth_note_on_layers()`). A new key transposes from
  the next step; letting go of every key stops it.
- Up to four automation lanes (one 7-bit value per step, optional glide), mod sources SRC_SEQ_A..D (bipolar). Steps
  × (3 + lanes) share 240 bytes: 64 steps alone, 60 / 48 / 40 / 34 with 1-4 lanes.
- Storage: PATCH_COUNT 400 → 200; patch n's sequence lives at 0x0C800 + 256 n ("P8S1", the stock banks C and D).
  The user's four C patches were removed (backups exist). Saved and loaded with the patch; cleared with it.
- SysEx 48 / 47 (settings), 46 / 45 (steps, 8 at a time); `pro800 seq` prints it. Edited only on the web page:
  a Sequencer section with a piano-roll picture, a 16-step page grid (on, note, gate, velocity cells that drag),
  and lanes you draw across with a destination menu each.
- Checked on the Mac (real `seq.c` / `lfo.c`, synth stubbed): steps at 125 ms (1/16, 120 BPM), 50 % gates, rests,
  transposing from the next step, ties legato (new note before the old one's release), layer filter, smooth lane
  halfway 0.50. Web page checked against a simulated synth.
- Web: the mouse wheel now adjusts only a control that has focus; scrolling over the step grid used to edit cells.

## Knob ranges, curves and units review (0.7.1, 2026-10-28)

- Reviewed every knob. Changed: FIL ENV AMT squared around the centre (±72 st kept: half-way 18, a quarter 4.5;
  was linear, 1.45 st per knob step) and PMOD FIL ENV squared (48 st kept: half 12; the useful 0-12 st was the
  bottom quarter). Saved patches' amounts come out smaller except at zero and the ends.
- Panel display units: M TUNE cents, FIL ENV AMT signed semitones, PMOD FIL ENV semitones, CUTOFF Hz / kHz (a point
  from 1 kHz on), LFO FREQ Hz, GLIDE time or OFF. Web editor matches (cutoff in Hz/kHz, amounts in semitones).
- New per-patch settings (patch bytes 250 / 251, 0 = old behaviour): pitch bend range 1-12 semitones (default 2)
  and unison detune 0-50 cents between the outermost voices (default 14 = the old fixed 2 cents between 8
  neighbours). Settings keys 7 `bEnd` / 8 `dEtU`, host settings 25 / 26, web Voice section, `pro800 set bend|detune`.
- Not changed, need measuring (group B): resonance (self-oscillation point), mixer levels and noise (dead zone,
  taper), M VOL (taper), PW (duty against the knob, silent ends), PMOD OSC B (depth curve).

## Expression, voice modes, morph, sequencer extras (0.7.2, 2026-10-28)

- Poly aftertouch per voice: poly pressure sets the pressure of the voices playing that note; SRC_AFTERTOUCH is
  the larger of a voice's pressure and channel pressure.
- Voice modes per layer: poly / mono (retrigger) / legato, note priority last / low / high (also for unison, which
  is the mono path on all the layer's voices). Patch byte 30, host settings 27-28, Settings key 9.
- Drift 0-99: per voice, a new random target every 0.4-2 s approached with a 0.5 s time constant; ±15 cents of
  pitch and ±1 semitone of cutoff at 99. Patch byte 26, setting 29, Settings key 0.
- Scale quantize: 12 scales and a root; the note plus the common pitch modulation snaps to the nearest scale note
  (ties go up). Patch byte 29 (scale | root << 4), settings 30 / 31.
- Morph: a target patch (byte 27, target + 1; SysEx 44 sets it, setting 35 reads it) whose knob values load into
  RAM; the Morph destination (24, per layer from the last voice) fades every knob towards them before the engine
  reads them. Switches now u16 at 24 so bytes 26-27 are free.
- Sequencer: steps are 4 bytes (extra: probability in eighths, 1-4 ratchets); max steps 60 / 48 / 40 / 34 / 30
  with 0-4 lanes; arpeggio modes (header byte 11) walk the held notes, a chord within 30 ms restarts step 1.
- MIDI: CC map for every knob (to the channel's layer or the edit layer), CC 64 sustain (holds keys and sequence),
  bank select + program change.
- Checked on the Mac (real synth.c / seq.c / lfo.c, hardware stubbed): mono low priority 60+64 → 60, then 55 → 55,
  back to 60; legato high 60 64 62 → 64; C major key 61 → 62; A minor pentatonic 57 + 6 st → 64; morph to a
  +12 st target → 72, halfway 66; poly pressure on one note opens only its VCA; drift 99 stays within ±0.15 st;
  4 ratchets → 4 note starts; probability 4/8 → 93 of 200 steps; arp up-down over a chord 60 64 67 64 60 64 67 64.
- Web: Voice card (voice mode, priority, drift knob, scale, root, morph to / by, Mutate), sequencer Prob / Ratch
  rows, Arpeggio, Euclid fill, Record from the page's keys or the router, Reset steps; Euclid on the LFO step grid;
  Morph in the Mod matrix.

## Backlog

- **Per-waveform levels within an oscillator**: not possible from firmware: the waveform switches are on/off logic
  lines to the voice board. Workaround: put the second waveform on the other oscillator and use its level.
- Hardware SPI to the DAC80501 (fails above 0.33 MHz; so does 1 MHz bit-banging: probably the board).
- Patch names from the panel (the keypad as a multi-tap keyboard?); today: the web page or `pro800 patch rename`.
- LFO fade-in / delay per LFO (today: Env 3 → LFO amount in the matrix does it per voice).
- MIDI CC map for the parameters (so a DAW or the Move can automate them), and program change → patch.
- MPE / 8-channel mono mode on top of the mod matrix (per-voice pitch bend and pressure).
- Arpeggiator? (Arp Up-Dn is LFO select now; Arp Assign is MOD ASSIGN.)
- More matrix sources: LFO 1 / LFO 2 unipolar, key-down gate, poly pressure per voice.

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
