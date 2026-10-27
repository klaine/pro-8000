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
- frequency knobs ±24 semitones in semitone steps; fine (B) ±1; master tune ±1
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
- Checked: load + play A00/A01/A02/B04; save A01 to an empty slot and compare records: identical apart from the
  low byte of a few knob values (1/65536 rounding) and M VOL (not applied); test slots cleared again.

## Backlog

- **Two more envelopes per voice** (asked 2026-10-27): memory (128 B) and processing (microseconds per pass)
  are no problem. Per-voice destinations are only pitch A, pitch B, cutoff and VCA level, so e.g. a pitch
  envelope per oscillator, a second filter envelope, or envelope → LFO amount; PW, mixer and resonance are
  shared wires (an envelope there could only follow the last note). UI idea: the keyboard-tracking switch picks
  which envelope the filter ADSR knobs edit (with pickup), keyboard tracking moves to the Settings menu.
- **MOD ASSIGN / mod matrix** (asked 2026-10-27; covers per-destination LFO depths). Arp Assign becomes MOD
  ASSIGN (LED blinks while active): (1) a number key picks the source: 1 LFO (the layer's), 2 filter env, 3 amp
  env, 4 velocity, 5 mod wheel, 6 aftertouch, 7/8 the extra envelopes, 9 random per note, 0 key position;
  (2) touching a knob or destination button picks the destination (in assign mode it selects only: no parameter
  change, no routing change; knobs pick up again afterwards); (3) the VALUE encoder sets a bipolar depth (-99..99,
  no pickup; 0 removes the route); MOD ASSIGN again leaves. Per layer. The LFO routing buttons with source 1 give
  per-destination LFO depths, LFO AMOUNT stays a master. Per-voice destinations: pitch A/B, cutoff, VCA and
  software parameters (envelope times/levels, LFO rate/amount, glide); the shared wires (mixer, PW, resonance,
  poly-mod amount) only take shared sources (shared LFO, mod wheel, last note's envelope), else the display shows
  `noPE`. Browsing: touching a destination with a source picked shows its depth; turning VALUE with no source
  steps through the active routes.
  Envelopes → LFO depth (touch LFO AMOUNT for all destinations, or one LFO button for that one) and → LFO rate
  (per voice, with `PoLY` retrigger): each voice scales its own LFO by its own envelope, e.g. delayed vibrato per
  note, an attack wobble that settles (negative depth), filter motion growing on held notes. LFO → PW only from
  shared sources. Best with the two extra envelopes, so the filter and amp envelopes stay free.
- Hardware SPI to the DAC80501 (fails above 0.33 MHz).
- Patch names: set from the Mac or the panel; import the factory bank into empty slots.

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
