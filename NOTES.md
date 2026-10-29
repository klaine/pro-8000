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
| MIDI | DIN in (opto U3) → PA1 = UART4_RX; `MIDI_THRU` from PA0 = UART4_TX and the input, gated by `MIDI_SEL` PB4 (0.7.6); MIDI channel DIP `MIDI_CH_0..3` PD9–PD12 |
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
  finished, was real and fixed (wait for RXNE per byte) but wasn't the whole story. (Corrected 2026-10-28: 0.0.2's
  bit-bang was 500 kHz, two 1 us half-bits, not 1 MHz, so every test agrees: fine up to ~0.5 MHz, wrong from
  0.66 MHz, whatever drives it.) Unresolved; see "The CV path's speed" (0.7.4).
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

## Faster envelopes: a split CV refresh (0.7.3, 2026-10-28; built, not flashed)

- The 2.1 ms CV refresh was most of each pass, and the envelopes moved once per pass. Per channel: ~74 us through
  the DAC80501 (pitch A, pitch B, cutoff: 24 channels), ~20 us through the on-chip DACs (8 VCAs, 9 global).
- Now `refresh_cvs()` (main.c) runs the amp and filter envelopes (`synth_envelopes()`) four times per pass between
  the parts of the refresh (`sh_refresh_pitch / cutoff / vca / global`): VCAs every tick, cutoffs every other tick,
  pitch two voices per tick, the global CVs once. `synth_update()` still does everything else once per pass and
  leaves each voice's VCA gain and cutoff (without the envelope) for the ticks. Envelope rates are now per second
  (`stage_rate`, `step`), so any dt works.
- A voice started since the last pass gets its pitch written first, so its VCA never opens on the old note. Silent
  voices' pitch and cutoff are refreshed only every 8th pass, which makes passes shorter when few voices sound.
- Expected with all 8 voices sounding (from the per-channel costs, not measured yet): pass ~3.4 ms (was 2.4),
  VCAs every ~0.9 ms, cutoffs ~1.7 ms. Mac model: an instant attack is fully open after 0.8 ms (was 2.4), 12 VCA
  steps in the first 10 ms of a decay (was 4), decay times unchanged (knob 50 %: 312 ms to -60 dB, was 314).
- To check on the synth: `pro800 timing` with 8 notes held and with none; `play --record` with a 0 ms attack and
  a short decay, to see how much the voice board smooths the steps.

## The CV path's speed, and a probe for it (0.7.4, 2026-10-28; built, not flashed)

Compared with p600fw: its 2 kHz timer interrupt advances every envelope and the LFO and writes every voice's four
CVs each 0.5 ms (vibrato, PW, glide, MIDI at 500 Hz), cheap because the Prophet-600's DAC is a parallel bus. Ours
(0.7.3, 8 voices sounding): VCAs ~0.9 ms, cutoffs ~1.7 ms, pitch and LFOs ~3.4 ms. The engine isn't the limit
(0.23 ms a pass): a pitch or cutoff write is ~74 us, 48 of them the 24-bit DAC80501 frame at 500 kHz, plus 6 us
settle and 12 us sample. The DAC is rated tens of times faster, so the limit is something not found yet.

- 0.7.4 adds a probe (SysEx 6C / 6D, RAM only, back to the default on reboot): bus bit-bang or hardware SPI3,
  bit-bang data change at SCLK rising (mode 0, as before) or just after it falls (mode 1: a full low time of setup),
  SCLK high/low times, SPI mode and divider, GPIO speed, SYNC wait, and the S&H settle/sample waits.
- `pro800 dac-test` sweeps it in direct mode, silent: 36 bit-bang buses (0.5-10 MHz, both modes, GPIO speed 0 and
  3) and 28 SPI ones (modes 0-3, 0.16-10.5 MHz), each judged by oscillator A1's pitch at three CVs against the
  default (good within 3 cents); then S&H waits from 6/12 us down to 0.5/1 us on the fastest good bus, judged by
  how much FIL8 (the DAC80501's previous write) pulls A1's pitch. A few minutes.
- What the result decides:
  - A frame of ~3-6 us (SPI or bit-bang at 4-8 MHz) and waits around 2-4 us: a channel ~10 us, all 40 in ~0.5 ms.
    Then the refresh moves into a 2 kHz timer interrupt like p600fw: envelopes, LFO output and every CV each
    0.5 ms; routes and the rest of the engine at 0.5-1 kHz in the main loop. The interrupt and the main loop then
    share voice state, so note-on / off and the envelope ticks need a short critical section.
  - Still ~0.5 MHz: keep 0.7.3's split refresh; only shorter S&H waits help (each us is ~40 us a refresh).
- If nothing faster works, the next suspects need a scope: SCLK/SDIN edges at U46, and whether anything else
  hangs on PC10 / PB0 / PA15.

## Fixes from a code review (0.7.5, 2026-10-28; built, not flashed)

- Clock: the USB interrupt takes the realtime bytes (`usb_midi_realtime()`) and counts and stamps every timing clock.
  The stamps were up to a pass late, so the tempo estimate wandered by up to 0.9 %. Ticks could also be lost: the
  64-packet ring overflowed during Tune, which shifted the grid until the next Start.
- `clock_update()` (run from LFO 0's update) works out the position from the count and the time since the last tick.
  It runs whichever LFOs are synced: before, a synced LFO 2 with a free LFO 1 only moved once per tick.
- After Start, the next clock is the downbeat, tick 0. It used to be tick 1: 21 ms early at 120 BPM.
- The clock counts as stopped two intervals after its last tick, and the main loop decides that, so the cycle
  counter's 25.6 s wrap no longer pulls a free-running synced LFO back.
- The grid-following LFO compared the new grid with the last update's phase and so settled up to a pass (3.4 ms)
  ahead; now it compares the grid with where the phase lands.
- `fast_exp2()` returned -inf or huge negatives below -127 (the exponent field wrapped). An envelope step after a
  stall of 38 ms or more, with a decay under 4 ms, then dropped the VCA to 0, pinned it at full or left it stuck.
  It's now 0 below -126 and at most 2^127.
- After Tune (panel or SysEx 75) the ring held only the first packets that came in meanwhile, so note-offs could be
  missing. They're all dropped now (`usb_midi_flush()`), and the sequence's keys and the sustain pedal let go.
- Channel mode: a note-off releases only its channel's layer (`synth_note_off_layers()`, `synth_channel_layers()`).
  The sequence keeps each key's layers and releases only its own notes; sustained keys remember their layers.
- Checked on the Mac against 0.7.4's sources (real lfo.c, synth.c, seq.c and fastmath.c, hardware stubbed, 120 BPM,
  3.4 ms passes):

  | Case | 0.7.4 | 0.7.5 |
  |---|---|---|
  | LFO 2 at 1/16, LFO 1 free | 102 frozen passes, 15 ms off the grid | none, 0.00 ms |
  | Both synced | 24.7 ms early on average | 0.00 ms |
  | After a 2.5 s stall | 59 ms off | 0.00 ms |
  | Clock stopped, 35 s on | a 62 ms jump at the wrap | none (drifts 19 ms: float position) |
  | Quarter note estimate | 0.4965-0.5046 s | 0.5000 s |
  | 38.5 ms stall in a 3 ms decay | VCA at 0 for the rest of the held note | at sustain |
  | Channel mode, C3 on channels 1 and 2, channel 2 lets go | both stop | channel 1's sounds on |
  | ... with the sequence on layer 1's C3 | the sequence stops, channel 2's C3 sticks | both right |

## 5-pin MIDI in (0.7.6, 2026-10-28; built, not flashed)

- Sheet 1: the MIDI IN socket's opto-isolator U3 (TLP2368, output pulled up by R10, 4k7) drives PA1, which is UART4_RX
  (AF8). `MIDI_THRU` comes from U4A (74LVC1G08), the AND of PA0 (UART4_TX) and the input through U5A (74LVC1G125,
  output enable = `MIDI_SEL` on PB4, active low; R15 pulls its output high while disabled): THRU echoes the input while
  `MIDI_SEL` is low, and PA0 can send on it. Neither is driven yet.
- `src/din.c`: UART4 at 31250 baud (BRR 1344 from the 42 MHz APB1), RXNE interrupt (IRQ 52) at USB's priority 0x80,
  so the two interrupts that feed `lfo_clock_tick()` never nest. Realtime bytes go straight to main.c's `realtime()`
  with the cycle counter, as from USB; other bytes queue in a 256-byte ring (about 80 ms of a full stream). The main
  loop parses them, with running status, into USB MIDI event packets, and `handle_midi()` reads USB first, then DIN.
  Channel messages only: SysEx and system common are skipped, so the editor, the tool and updates stay on USB. A
  framing error drops the byte; tuning flushes the ring with USB's.
- USB stays a device (`GUSBCFG_FDMOD`) on a USB-B socket with VBUS in, so a USB keyboard can't plug in directly: the
  DIN socket is the way to play without a computer.
- Checked on the Mac (the real `din.c`, registers stubbed): BRR 1344; running status, velocity-0 note-ons, program
  change and channel pressure (one data byte), bend, CCs, poly pressure; a clock tick inside a note-on goes to the
  handler and the note still completes; SysEx and song position skipped; a framing error drops the byte; an overflow
  keeps the first 255 bytes; flush clears running status. Build 48,376 bytes. Not flashed or played from a keyboard yet.

## Second review: knobs, channel mode, Song Position, a pipelined S&H refresh (0.7.7, 2026-10-28; built, not flashed)

From a second review, each point checked against the code first.

- MOD ASSIGN: `synth_freeze_knobs()` put the knobs in pickup, but `read_panel()` took any knob within 1.5 % of its
  value back on the next pass, so in manual mode turning a knob to choose a destination also changed it. Now no knob
  picks up while assigning (`synth_freeze_knobs(1)`); leaving MOD ASSIGN lets them pick up as usual.
- Channel mode: pitch bend, mod wheel, channel and poly pressure and the sustain pedal reach only the layer their
  channel plays (`synth_channel_layers()`); the other modes as before.
- `send_sysex()`: a write that can't go out within 20 ms (was 100 ms per chunk) drops the message, and later ones at
  once until a write goes through. Checking `configured` wouldn't do: with no VBUS sensing or suspend interrupt it
  stays set when the cable is pulled; it's `tx_busy` that sticks.
- The LFO clock position runs modulo 2304 ticks (24 bars, a whole number of cycles of every division; 768 would
  break the dotted ones), so the grid keeps its precision however long the clock runs.
- Song Position Pointer (USB CIN 3, and parsed in the DIN interrupt; both drivers' clock handler now takes a data
  word) and Continue: the count resumes at the song position, or where the clock stopped. After Start or Continue the
  LFOs on the grid hold where the next clock lands, for two intervals at most (in case no clock follows): before, a
  synced LFO ran on while it waited and then slid back. (Fixed after the review: the window is one-shot. It used to be
  judged from the elapsed cycles alone, and with no clock following a Start the cycle counter's 25 s wrap reopened it.)
- `CLoC` before any clock runs its division at 120 BPM (`lfo_quarter_seconds()`), as the cycling envelopes and the
  sequence do. It ran free at the knob read as Hz, while the display showed a division.
- Stepped knobs (FREQ A / B; LFO FREQ while its LFO is synced): `synth_knob_snap()` changes the step only a quarter
  step past the half-way point and reads the knob as the middle of its step, so the engine, the display and a saved
  patch round it the same way. A first try kept the hysteresis on the parameter: the step then depended on the
  direction the knob came from and on the patch loaded before.
- Voice stealing takes the quietest released voice (was the oldest). A "VCA dip" for a stolen voice wasn't done:
  closing the VCA for a tick is a step in amplitude, a click of its own, and the pitch change of a stolen voice with
  its VCA open sounds like legato.
- Panel switches count after 15 ms steady (was two reads a pass apart, about 1 ms with no voice sounding); the MIDI
  channel, pedal and SYNC IN lines as before.
- The display writes only the bytes that changed (`panel_write_range()`): a digit is 2 bytes plus the address, about
  0.8 ms, instead of 16 (3.5 ms); an LED blink 1.
- CUTOFF and NOISE (direct ADC inputs) are read at every poll. The muxed pots, read every 32 ms, glide to each reading
  over 32 ms instead of stepping.
- S&H pipelining (`sh.c`): a channel keeps sampling while the next DAC80501 frame clocks in; `dac16_write()` calls a
  hook two bits before the frame ends, which holds it. At each DAC80501 channel's address the VCA there (DAC2) and a
  global CV due there (DAC1) sample alongside. `sh_refresh_vca(skip)` and `sh_refresh_global()` write only what wasn't
  written alongside; `sh_hold()` before the bus probe drives the DAC.
- The fast ticks (`synth_tick()`, was `synth_envelopes()`) also advance the LFOs and Env 3 / 4, and evaluate the
  routes from envelopes and LFOs into pitch, cutoff and the VCA (`fast_route()`). The pitch of the two voices written
  in a tick is worked out in that tick. `synth_update()` keeps the slower terms per voice.
- Checked on the Mac. S&H simulator (GPIO B / D pins, a 50 us DAC80501 frame whose output changes at 48 us, both
  on-chip DACs, in 168 MHz cycles), one pass with 8 voices: 0.7.6 2914 us, 0.7.7 1836 us. Every channel held the
  value it was loaded with; no DAC changed while a channel it feeds sampled, the address never moved during a sample,
  no two muxes of one DAC sampled at once; the shortest sample on a settled DAC was 18.0 us (0.7.6: 12 us after a 6 us
  settle). VCAs are written 5-6 times a pass (were 4), cutoffs twice, pitch and the globals once. Engine (the real
  synth.c, lfo.c and seq.c, hardware stubbed), against 0.7.6:

  | Case | 0.7.6 | 0.7.7 |
  |---|---|---|
  | MOD ASSIGN in manual mode, a knob touched | cutoff 0.50 → 0.73 | stays 0.50; picks up after |
  | Channel mode, channel 2 bends up | both layers +2 st | layer 1 0, layer 2 +2 |
  | All 8 releasing, a 9th note | takes the oldest (loud) | takes the quietest |
  | FREQ A on a step's edge, ±10 LSB noise, 10 s | 159 semitone jumps | 0 |
  | LFO FREQ (synced) on a division's edge, same noise | 148 rate changes | 0 |
  | LFO (18 Hz) → cutoff at 99 | a new cutoff once a pass, steps up to 23.6 st | twice a pass, up to 11.8 st |
  | Stop, Song Position 66, Continue; a 1-4 LFO | 51 ms off on average, 250 at worst | 0.0 ms |
  | `CLoC` at 1-16 before any clock | 19 Hz (the knob as Hz) | 8 Hz (120 BPM) |
  | 3 hours of clock, a 1/32 LFO's steps | off by up to 0.0017 cycle | 0.0001 |
  | A plain held chord | | the same CVs |

  The 0.7.5 grid checks still give 0.00 ms. Build 51,056 bytes.
- Expected on the synth with 8 voices (from the model, not measured): a pass of about 2.3 ms (was about 3.4), VCAs
  about every 0.5 ms, cutoffs about every 1.2 ms. To check: `pro800 timing` with 8 notes held, and `pro800 check`:
  if the bus toggling during a sample pulled the held CV, the oscillators would read off.
- Not done: the sequencer and MIDI between the ticks (note timing still moves by up to a pass, now about 2.3 ms),
  hardware I2C1 / SPI4 for the panel and the EEPROM, the panel ideas (tap tempo, compare, Sync buttons).

## On the synth: 0.7.7 flashed, tables re-tuned, tick trims (0.7.8, 2026-10-28)

- 0.7.7 was the first build on the hardware since 0.7.2 (0.7.3-0.7.6 had only been built). Flashed with
  `pro800 install`; the firmware answered after its start-up.
- **The stored tuning tables don't fit the pipelined refresh.** `pro800 check` read every oscillator +3 to +28
  cents sharp, rising with the note (a gain error: the held CV comes out a fixed fraction higher than the tables,
  made under 0.7.2's 12 µs sample with an idle bus, expect). After `pro800 tune` the check was within 0.9 cents
  everywhere: the pipelined sample holds a consistent voltage, just not the old one. So `TUNING_FORMAT` is now 3,
  and the first start after this update re-tunes once (about 25 s, `tUnE`), as with no tables.
- Timing measured on the synth (`pro800 timing`, notes held from the tool), 0.7.7:

  | Voices sounding | Engine | CV refresh (incl. the ticks) | Pass | Longest in 2 s | Passes/s |
  |---|---|---|---|---|---|
  | 0 | 255 µs | 995 µs | 1272 µs | 3123 µs | 658 |
  | 1 | 225 µs | 1148 µs | 1394 µs | 3664 µs | 620 |
  | 4 | 225 µs | 1768 µs | 2038 µs | 4636 µs | 460 |
  | 8 | 270 µs | 2710 µs | 3000-3100 µs | 4860 µs | 325 |

  Against 0.5.6-0.7.2's constant 2.3 ms pass: with eight voices the pass is longer (the four ticks each do the
  LFOs, four envelopes and the fast routes for every voice, about 0.2 ms a tick, which the Mac model left out), but
  the VCAs move every 0.77 ms and the cutoffs every 1.5 ms instead of every 2.3; with one voice 0.4 and 0.8 ms.
  Pitch per voice and MIDI are still once a pass, so at eight voices those are a third coarser than before.
- Audio through the Move (`pro800 play --record`): notes at about -28 dBFS over a -58 dBFS floor; the mix of A + B
  reads 2 to 5 cents flat at the output with the knobs centred, while the comparator check is within a cent per
  oscillator (the comparator is the reference; the output figure averages the two oscillators and the recording).
- Recording analysis of the pitch staircase (a 5 Hz, ±1 semitone vibrato at C7, pitch per period from the zero
  crossings of the band-limited fundamental, pure Python): the staircase is below what the chain can show. Done
  first on the patch that happened to be loaded, then on the init sound (osc A saw only, filter open) with 0.7.8,
  one voice and eight, plus a steady C7 as the control. The control has the same period-to-period jitter as the
  vibrato takes (±20 cents, a 3-period pattern, about 350 Hz) and the same lines in its spectrum (670 and 350 Hz
  either side of the carrier, -30 and -37 dB against the carrier band; -22.8 dB all together between 150 and 800
  Hz, against -22.5 to -22.8 with the vibrato): the oscillator and the recording chain, not the stepping. So the
  zipper sidebands of a pass-rate staircase are below -23 dB relative to the carrier band and below the chain's own
  lines, and steps shorter than about 3 ms can't be resolved period by period. The step rates come from the timing
  probe. On init the output reads 130.81 / 261.59 / 523.37 / 1046.94 Hz for C2-C6 (within 0.7 cents); the
  2-5 cents flat measured earlier was that patch's A + B mix. The scripts stayed in the session's scratchpad.
- 0.7.8: the timing reply carries an eighth value, the pass's four `synth_tick()` calls together, so the tick cost
  can be read directly. Trims in the ticks: a silent voice (amp envelope idle) only gets its VCA CV set to 0; each
  LFO's value is taken once per layer per tick when its phase is shared (per voice only in per-voice trigger mode);
  the fast routes are evaluated only in a layer that has one; Env 3 / 4 advance only in a layer whose routes read
  them; per-voice LFO phases advance only in per-voice mode; `tuning_note_cv()` reads a per-row segment cache
  (CV and codes per octave per segment, built at `tuning_load()`) instead of scanning the table and dividing every
  call. Build 51,896 bytes.
- 0.7.8 on the synth: `install` re-tuned by itself before answering (the format bump), and `check` is within 0.9
  cents on every oscillator. Timing (layers off; the boot had loaded a stacked patch, which doubles the voices):

  | Voices sounding | Engine | CV refresh | of which the 4 ticks | Pass (average over 1 s) | Longest in 2 s |
  |---|---|---|---|---|---|
  | 0 | 144 µs | 648 µs (2234 on the 8th pass) | 93 µs | 1044 µs | 4226 µs |
  | 1 | 145 µs | ~1000 µs | 153 µs | 1315 µs | 4289 µs |
  | 2 | 156 µs | 1188 µs | 202 µs | 1552 µs | 3383 µs |
  | 4 | 177 µs | 1666 µs | 300 µs | 2016 µs | 4480 µs |
  | 8 | 230 µs | 2604 µs | 462 µs | 2932-2941 µs | 4688 µs |

  Against 0.7.7: idle 1044 µs a pass instead of 1519, one voice 1315 instead of 1612, four 2016 instead of 2173,
  eight 2935 instead of 3080. The ticks are 16 % of the eight-voice pass now (about 12 µs per sounding voice per
  tick); the rest is the bus: 32 DAC80501 frames at about 60 µs each (48 µs of bits plus the two 1 µs SYNC waits
  and the per-bit call overhead), 12 VCAs alone at 18 µs, and the globals. The next lever is the frame itself:
  timing each bit from the cycle counter instead of delaying after each GPIO write would take the frame to 50 µs,
  about 0.3 ms a pass with eight voices; the 8th-pass refresh of idle voices (the "longest pass") could be spread
  over passes too.
- The longest-pass figures above are the every-8th pass that refreshes idle voices plus a display write.
- 0.7.2 measured the same way (flashed back for it, then 0.7.8 again; each boot re-tuned): idle engine 221 µs,
  refresh 845 µs, 1416 µs a pass on average, longest 5339 µs; eight voices engine 246 µs, refresh 3290 µs, pass
  3542-3586 µs, 3663 on average, longest 5417 µs, 273 passes a second. Everything moved once a pass then. So
  against the firmware the synth ran before today: idle 1416 → 1044 µs, eight voices 3663 → 2935 µs; the VCAs move
  every 0.73 ms instead of every 3.7 (5×), the cutoffs every 1.5 ms (2.5×), the LFOs and fast routes every tick,
  and pitch and MIDI once a 2.9 ms pass instead of a 3.7 ms one. (The 2.3 ms pass of 0.5.6 in the notes above
  predates the sequencer, the second LFO and the 0.7.x engine.)

## dac-test run on the synth (2026-10-28, 0.7.8)

The 0.7.4 sweep, run for the first time (`pro800 dac-test`, then `--clock --bus-only` for the edge of the range,
asymmetric clocks and no SYNC wait; both logs were in the session's scratchpad):

- **Every bus above 0.6 MHz fails outright**, 7000-8400 cents off (the DAC gets wrong data), bit-bang and SPI3
  alike, both data-change modes, GPIO speed 0 and 3. SPI3 works only in modes 1 and 2 (the DAC latches on the
  falling edge) and only at 0.16 and 0.33 MHz; modes 0 and 3 fail at every speed. Bit-bang 0.5 MHz is within 0.5
  cents of the default, as it must be (it is the default).
- **The limit is the bit period, not an edge.** Symmetric 154/154 cycles (0.55 MHz, 46 µs a frame) and 140/140
  (0.60 MHz, 42 µs) work, 126/126 (0.67 MHz) fails. Asymmetric clocks with the same period behave the same:
  168/112 and 112/168 (280 cycles a bit) work, 168/84 and 84/168 (252 cycles) fail, 140/112 and 112/140 fail. So
  whatever sits on SCLK or SDIN needs about 1.65 µs per bit however the time is split, which doesn't look like a
  simple RC on one edge. At 0.60 MHz the pitch error is 0.9-1.2 cents against 0.0-0.6 at 0.50-0.55: marginal.
  No SYNC wait (`sync_us` 0) works at 0.5 MHz.
- The S&H-wait part of the sweep is moot since 0.7.7: it judges A1, whose sample now lasts the whole next frame
  whatever the settle and sample settings; those only bound the channels written alone (VCAs, globals). It read
  within 0.2 cents down to 0.5 / 1 µs, which says nothing about the on-chip DAC channels.
- What this decides: the 2 kHz interrupt refresh with every CV each 0.5 ms (the 0.7.4 plan) is off unless the
  board is changed, and a scope on SCLK / SDIN at U46 is the only way to learn what limits it. What's left on the
  bus in firmware: the per-frame overhead (a frame measures about 60 µs for 48 µs of bits: time the bits from the
  cycle counter instead of delaying after each GPIO write), no SYNC wait, and possibly 154/154 cycles with margin
  to spare; together a frame of about 46 µs, about 0.45 ms off the eight-voice pass (2.9 → 2.5 ms).

## Timing and engine backlog, LFO fade, new sources, a voice per channel and MPE (0.7.9, 2026-10-28)

- **The DAC80501 frame** (`dac.c`): each half-bit ends at a cycle-counter deadline, so the GPIO writes and the loop
  add nothing: 24 × 336 cycles = 48 µs. No wait around SYNC (`sync_us` 0; the sweep showed it works, the DAC's own
  minimum is 20 ns). The held CV moved about a cent with it, so `TUNING_FORMAT` is 4: one re-tune after the update.
- **MIDI and the sequence between the ticks** (`refresh_cvs()`): before ticks 2-4 of a pass `handle_midi()` runs
  and the sequence advances (`advance_sequence()`, by its own elapsed time, bounded at 50 ms), and a voice that
  started gets its pass terms (`synth_prepare_voices()` → `voice_slow()`, the per-voice half of `synth_update()`)
  and its pitch before its VCA. Note-on to pitch is now within a tick instead of a pass.
- **The idle refresh spread**: one silent voice's pitch and cutoff per pass, in turn, instead of all eight every
  8th pass. The longest pass fell from 4.2-4.7 ms to about 3 ms idle and 4.2 with eight voices (the display write
  is most of what's left); the average is the same.
- **The lone channels' S&H waits** are 3 + 6 µs (were 6 + 12). Measured through the Move on the init sound with
  `pro800 sh-waits` (new: sets them, RAM only): the level and spectrum are identical from 6/12 down to 2/3 µs
  (-28.0 dBFS, the same lines), and 1/1 collapses (-37 dBFS, the VCA undercharges). 3/6 keeps nearly twice the
  working margin.
- Measured on the synth (init sound, layers off):

  | Voices | Engine | Refresh | of which ticks | Pass average | Longest in 2 s |
  |---|---|---|---|---|---|
  | 0 | 143 µs | 854 µs | 23 µs | 1019 µs | 2870 µs |
  | 1 | 133 µs | 1030 µs | 26 µs | 1196 µs | 3034 µs |
  | 8 | 176 µs | 2133 µs | 260 µs | 2398 µs | 4184 µs |

  Against 0.7.8: 1044 → 1019, 1315 → 1196, 2935 → 2398 µs. Against 0.7.2: 1416 → 1019 and 3663 → 2398.
- **LFO delay and fade-in** (`lfo_delay[2]`, `lfo_fade[2]`, 0-99 → 0, 20 ms .. 5 s): per voice, from the note's
  start (`lfo_age`, reset with `fresh`); the gain multiplies the LFO wherever it's read for that voice, and the
  single-wire PW follows the most recent voice's. Stored in the patch record's two master-volume pot slots (never
  loaded as pots) behind a flag in byte 31, so older records read as 0. Host settings 32-35, which moved
  env-select, preset-mode, patch and morph to 36-39 (tool and page updated).
- **Sources** appended: `SRC_LFO_UNI`, `SRC_LFO2_UNI` (0..1), `SRC_GATE`, `SRC_TIMBRE` (CC 74). Host-only, like
  the sequence lanes.
- **Layer modes 4 and 5**: `8CH` (channels 1-8 → voices 1-8, mono, own bend at the bend-range setting, pressure,
  CC 74; channels 9-16 ignored) and `nPE` (MPE lower zone: channel 1 master, channels 2-9 → voices, 48 semitones
  of bend; the master's bend, wheel, pressure and pedal reach every voice and its notes play poly). Per-voice
  `bend` and `timbre` in `voice_t`; `synth_channel_voice()` and the `synth_voice_*()` entries; `key_off()` by
  channel. Checked on the synth: a member channel bent by 12 semitones read +1200.8 cents, the master's +2
  semitones on another channel's note +200.0, an unbent member -0.7.
- The dac-test harness and the 0.7.5 engine harness from the other session were found in its scratchpad; they
  target 0.7.5-0.7.7 interfaces and weren't ported (see the backlog).

## Matrix editor, 28 sources and 46 destinations, macros, 128 patches of 768 bytes (0.8.0, 2026-10-28)

- **Patch storage, format 2** (`store.c`): 128 records of 768 bytes at 0, each a patch (first half, checksum at
  508) and its step sequence (second half from 512, its own magic and checksum), so a sequence is written alone and
  a load is one read. 16 routes per layer. The factory bank stays at 0x19000; the global block gains a format byte
  and the CC A / B numbers (within its checksummed 12 bytes). At the first start a format-1 global block has the
  patch area zeroed (384 page writes, about 2 s, `ForM` on the display); a stock or blank EEPROM still waits for
  `format` (`pro800 format --yes`, SysEx 5B, which replaced the stock import). One bank: `P  7`, `P128`; program
  change 0-127; up to three digits on the keypad. The 0.7.x patches are in `backup/eeprom-before-0.7.7.bin`.
- **Sources** appended (`mod.h`): bend, pedal, CC A, CC B, voice number, fixed, Macro 1, Macro 2. CC A / B follow
  controller numbers kept in the global block (settings 36-37; defaults 11 and 2; the tool's `set cc-a|cc-b`, the
  page's Voice section). **Destinations** appended: the 16 envelope stages (a time scales by 2^(4 × mod), 16× at
  depth 99; sustain adds), glide (the same scaling), detune (a wire, ±50 cents), volume (a wire; 0..1 sources count
  down from full like the VCA), Macro 1, Macro 2. The macros are evaluated in two phases of the slow routes: all
  routes into them, then the routes they feed; a macro is a bipolar source. The stage and glide routes are applied
  per voice in the ticks on a copy of the layer's rates, only for voices that have one (`env_modded`).
- **The panel editor** (`main.c`): touching an LFO or ADSR knob picks it as the source; VALUE scrolls sources, then
  destinations (Arp Assign takes a scrolled one; a touched one is taken at once); the knob touched last before Arp
  Assign is offered as the destination; the ADSR knobs are stage destinations (AMP SUS stays the VCA). Hold Arp
  Assign 0.6 s to browse the edit layer's routes (source / destination / depth frames of 0.6 s, VALUE steps, a
  short press edits, holding again leaves). The button's press starts a timer and its release is the short press.
- Checked on the synth by pitch, init sound, depth 50 into pitch (squared: 6.1 semitones): fixed +610.9 cents;
  CC 11 → Macro 1 at 99 → pitch: +612.3 with CC 11 at 127, −0.1 at 0; pedal down +612.5; bend → pitch 99 with the
  wheel up +2599.6 (2 of bend range + 24); voice → pitch in 8CH: channel 1 −0.6, channel 8 +612.7; the 17th route
  refused; save to P4, listed, loaded. The format ran at the first boot; the global block read format 2 with every
  slot zero. The panel editor itself isn't exercised from here (it needs the buttons).
- Build 59,576 bytes of 65,536: about 6 kB left in sector 4.

## Shift on Sync Source, the keytrack switch back, macro offsets (0.8.1, 2026-10-28)

- **Shift** (`main.c`): Sync Source held, or latched with two presses within 400 ms (LED; `SHFt` / `oFF`). On:
  `synth_set_shift(1)` sets `env_select` 3 (the filter ADSR knobs edit Env 3, the amp ADSR knobs Env 4, with pickup)
  and freezes every other knob, which `shift_turn()` turns into relative nudges of a secondary value: the first
  event of a knob under Shift only takes its position, later ones add the delta scaled to the value's range
  (fractions carried in `shift_acc`), the display shows the name for 0.5 s then the value. LFO FREQ / AMOUNT →
  the selected LFO's delay / fade, GLIDE → drift, M TUNE → detune, FINE → bend range, CUTOFF / RESONANCE → the
  macro offsets, A / B LEVEL → the CC A / B numbers. Buttons: Rec compare (the stored patch while held, the edit
  back on release, also if Shift went first), Perf undo (the edit before the last load or init; again redoes),
  Preset save to the current slot, Seq 1 / 2 copy the edit layer (`synth_copy_layer()`), 1-9 open that Settings
  page, 0 panic. Off: the knobs are in pickup, as after a load.
- **The keyboard-tracking switch is keyboard tracking again**: `read_panel()` takes the switch's position when it
  changes; a patch's value holds until then (`keytrack_switch_seen`). The `trAC` settings page stays.
- **Macro offsets**, per layer and macro, -99..99, added to what's routed into a macro; in the record at 70-73,
  host settings 38-41 (the offset + 99, so 7D now also takes a 14-bit value: `7D <setting> <v1> <v0>`; the tool
  and page send two bytes always), `pro800 set macro <layer> <1|2> <offset>`. The read-only settings moved to
  42-45.
- Checked on the synth: Macro 1 offset +99 / -99 with Macro 1 → pitch 50 read +610.4 / -612.0 cents; an offset
  of 40 saved to P8 and read back +40 after a load; the settings dump lists everything at its new index. Shift
  itself, the switch and the shifted buttons need hands on the panel.
- Build 62108 bytes of 65,536.

## Internal clock, tap tempo, clock source, the beat light (0.8.2, 2026-10-28)

- `main.c`: a 24 ppq generator (`clock_run()`, called before each tick and pass) stamps every tick with its
  scheduled cycle count and feeds `lfo_clock_tick()` exactly as the interrupts do, so the LFO clock, the cycling
  envelopes and the sequence see a crystal-steady clock whatever the loop is doing (up to four catch-up ticks a
  call). Sources: MIDI (the handler ignores realtime otherwise), internal, SYNC IN (`SWITCH_SYNC_IN_A` rising
  edges at 2 ppq set the tempo and re-phase the generator; untested, the lines have never been seen moving). Tap
  tempo on Sync Clock (2-4 taps within 2 s, the last tap is a beat), a hold restarts the bar
  (`lfo_clock_start()` and a tick now), Shift + Sync Clock cycles the source. The LED blinks on the beat from
  `lfo_beat_phase()`, whichever source. Source and tempo (tenths of a BPM) live in the global block at 16-18,
  outside its checksum and range-checked; settings 42-43 (the read-only ones moved to 44-47).
- Checked: LFO 1 synced at 1/16 into the VCA, internal clock, recorded through the Move: the envelope spectrum's
  line at 8.1 Hz at 120 BPM and 6.1 Hz at 90 (8 and 6 expected).
- **Flash: 63,552 of 65,536 bytes.** Sector 4 is nearly full. The tuning tables moved to the EEPROM in 0.6.x, so
  sector 5 (128 kB at 0x08020000) is free: `link.ld` could extend the app over it if the bootloader programs an
  image longer than 64 kB (to check with a padded build before relying on it; our update path erases sector 4
  only, `src/update.c`). Otherwise: drop the hardware-SPI probe path, trim the SysEx tables, or `-Os` already.

## The preset bank, and the tool writes sequences (2026-10-28)

- `presets/`: 64 patches as JSON (slot, name, group, notes, buttons, params per layer, settings, routes, sequence,
  morph), `install.sh` (one `build/pro800` command per step, from `lib/plan.py`, which validates every name and
  range against the tool's tables; `--dry-run`, `--only`), and a README with a line per patch. Designed from the
  manual's curves by a subagent, not auditioned: levels and cutoffs may want a pass on the synth. Eight groups:
  basses, leads, polys, pads, plucks, arps / sequences, FX, and curiosities (8CH, MPE, a wheel morph to P25, a
  split, a unison stack, a trance gate, a self-player on a scale, a two-macro patch).
- The tool gained `seq set key=value…` (SysEx 47 after reading 48), `seq step <n> …` (45) and `set morph <slot>`
  (44), which the installer detects in the help text; `slotNumber()` takes P1-P128 or 0-127.
- Installing the bank is about 5,600 commands, each a process that opens MIDI and waits for an ack: minutes. A
  `pro800 batch` mode reading commands from stdin on one connection would cut that; backlog.

## Modulation range arcs on the web page's knobs (2026-10-28)

- `dial()` draws an arc (`.k-mod`, radius 22.2, under the tick scale, 3.2 wide) from `modRange(knob)`: the edit
  layer's routes into that knob's destination, each route's full-depth swing scaled to the knob's own range
  (`DEST_SCALE`: pitch 24 of 72 semitones with the squared depth, cutoff 96 of 120, PW 0.5, LFO rates and glide
  4 of 10 octaves, envelope stages 4 of 12, the rest the full travel), summed; sources that swing either side of
  the middle spread both ways, 0-to-1 sources one way. The arc is an estimate (the knob curves aren't all linear in
  the summed domain), the dial's title lists the routes. Checked offline with injected routes.

## The bank redesigned for movement, spread and expression (2026-10-28)

- The first bank's leads, polys, pads and plucks sounded like an '80s demo: one saw, one filter, one envelope,
  nothing moving. `presets/DESIGN.md` has the principles now used across P1-P40 (from a look at how current analog
  polys are programmed: slow unrelated modulators, PWM that drifts, per-voice instability, layering, velocity and
  pressure into more than volume), mapped to the firmware: LFO 2 as the per-voice drift shape into B's pitch and
  the cutoff (`life()`), the voice-number source into B's pitch and PW so chords spread without unison (`spread()`),
  Env 3 as a slow second filter envelope (`swell()`), LFO 1 sine vibrato with delay and fade (`late_vibrato()`),
  velocity into cutoff, level and one of bark / attack time / decay (`touch()`), aftertouch into cutoff, vibrato,
  detune or resonance, often via Macro 1 (`press()`), and stack mode for a body plus a shine layer (Wide Lead,
  Poly Brass, Analog Strings, Wide Stack). The generator (`presets/lib/gen_presets.py`, now in the repo) carries
  them as helpers; P41-P64 keep their sounds (the sequences and curiosities are the point there).
- Lessons from the first bank: a poly-mod amount with no destination button does nothing (P9, P20); FM through
  poly-mod is exponential, so a 3:1 "tine" smears instead of ringing (P21 is subtractive now); Env 3 for what the
  filter envelope already does is a waste of a route (P19); PMOD FIL ENV with a filter sustain above zero leaves
  the note sharp by amount × sustain for its whole length (P8 read 0.76 semitones sharp; its kick is Env 3 → Pitch
  A now, which returns to zero). Both are in `presets/DESIGN.md`.

## P57 across its channels, macro knobs and the displays on the faceplate (2026-10-29)

- P57 Eight Channels played the same sound on every channel: the patch format has two layers, not eight, and the
  first version only differed per channel through bend, pressure and CC 74. In the 8ch mode voice = channel, so
  the voice-number source is what tells the channels apart; P57 now grades cutoff (+48 st by channel 8), the
  filter envelope's amount, the filter and amp decays, both sustains (channel 8 is a pluck) and the glide (channel
  1 slides, 8 doesn't) from channel 1 to 8. Measured at C3: channel 8 sustains 9 dB below channel 1 with the upper
  harmonics 5-6 dB stronger. Only per-voice destinations work for this: the shared pots take their modulation from
  the voice evaluated last (`last_mod` in `synth.c`), so `spread()` lost its pulse-width option (P17 and P23 used
  it; it was one constant offset). Written into `presets/DESIGN.md`.
- The web page's mod matrix has a knob for each macro: the macro's offset (the setting, as Shift + CUTOFF /
  RESONANCE on the panel), with a ring for what the routes into the macro can add and the inputs → outputs beside
  it. That was the backlog's "web macros panel".
- The faceplate took over the displays: each oscillator section shows its waveform, the filter section its
  response curve and the envelope the ADSR knobs edit (following Env select), the amplifier its envelope. The Scope
  section is gone and the Filter env / Amp env sections below keep only their mode menu and copy button.

## The web page's layout pass (2026-10-29)

- The filter and amp envelopes' Mode menus and copy buttons moved onto the faceplate, under each envelope picture
  (the filter one follows Env select, so it can set Env 3 / 4's mode too); their sections below are gone.
- The sections below: LFO 1 and LFO 2 side by side with Env 3 over Env 4 beside them at full width (their heights
  match), then Voice across the page as an even grid of labelled controls, then the mod matrix and the sequencer
  each across the page. The matrix grows with its routes, so nothing shares a row with it. LFO 1 got the shape
  buttons, rate, amount and routing (the LFO-MOD switches) that LFO 2 has, so the two read the same.
- The faceplate's four-column layout now needs 1280 px (the displays beside their knobs need the width; columns
  6.4 / 1.2 / 5.85 / 1.4); narrower, the oscillators go over the filter as before. Displays that take a section's
  spare height sit in a stretching box with the canvas absolutely filling it: a canvas sized from its own height
  feeds back into the row and grows on every repaint.
- Checked with a script measuring every section at 1800, 1500, 1290, 1200, 1100, 900, 600 and 420 px, with 0, 10
  and 16 routes: no overflow or sideways scroll, no blank area over 45 px except the Noise knob's box and the master
  tune column, which match the hardware panel. The macro knobs had been throwing on a missing helper, so their
  values never showed; fixed.

## A pad with an arp on top: the stack-mode key fix and the 7 + 1 voice split (0.8.3, 2026-10-29)

- In stack mode a sequence on one layer's keys took the whole key (`seq_key_on` returned 1 whenever the key reached
  its layer), so the other layer never played it: the user's P65, a slow pad on layer 1 with the arp on layer 2,
  played only the arp. `seq_key_on` / `seq_key_off` now return the layers they took and `main.c` plays or releases
  the rest on the engine. The pedal works per layer as before (a key let go under the pedal on layer 1 releases
  the sequence's layer 2 at once and layer 1 at the pedal's release). Host test with the engine stubbed: stack
  on and off, an arp chord, the pedal on one layer, split, the sequence on both layers, the sequence off.
- `voice_split()` in `synth.c`: in the two-layer modes a layer in mono or legato without unison (which only ever
  sounds its first voice) gets one voice and the other layer seven; otherwise four each. Every change of voice
  mode, unison or layer mode goes through `synth_all_off()` (copying a layer now too), so no voice changes layer
  while gated.
- Checked on the synth with P65 (recordings at 1-1.5 kHz, above the music the user was playing on top): C#6 held,
  the note's line reaches −22 dB as the pad opens, against −33 to −52 dB with layer 1 silenced (the arp alone);
  a held seven-note cluster C6-F#6 with layer 2 mono keeps all seven pad notes at −22 to −29 dB, and with layer 2
  poly (4 + 4) the first three fall to −33..−86 dB between the arp's visits. Eight voices on the init sound: 2423 µs
  a pass, the ticks 102 µs (no change). Build 64,352 bytes of 65,536: 1.2 kB left in sector 4.

## Checking the filters' tuning, and the flash room (0.8.4, 2026-10-29)

- Autotune has measured the eight filters since the first tuner (self-oscillating, resonance at maximum, mixer off,
  CVs 0x2000-0xD000), and every cutoff the engine writes goes through the filter's table (`voice_cvs()`), so
  keytracking and the voices already match. Only the oscillators were ever checked.
- The probe `78 <voice> <source> <note>` takes source 2 (the filter) from 0.8.4, and `pro800 check` runs a filter
  pass after the oscillators, as autotune measures them: resonance full, both mixer channels off, notes
  48-120 (131 Hz to 8.4 kHz). `--osc` or `--filters` runs one pass.
- Result (0.8.4, tables from an earlier tune): the eight filters match each other within about 6 cents at every
  note, and all track about 1 % shallow: -51 to -57 cents at note 48, -16 at 96, +4 to +6 at 120. The oscillators
  read +0 to +3 cents at C2 rising to +4 to +10 at C7 (±0.7 right after a tune), the same slope, so most likely
  drift since the tables were measured; a `tune` would show. Note 36 reads an octave high on some oscillators,
  the comparator's known stray edge below about 70 Hz, not a tuning error.
- What it can't show: at lower resonance the cutoff sits a little off the self-oscillation pitch. That shifts all
  eight filters alike, so tracking and the match between voices hold.
- **Flash: 64,360 of 65,536 bytes** (1,176 free). `-Oz` builds the same as `-Os`; `-flto` is 656 bytes over (it
  inlines more). The biggest functions are the MIDI/SysEx handler (6.4 kB), the panel's control events (4.3 kB)
  and the engine's update (4.0 kB); the probe-only code (dac-test bus switching, S&H waits) is well under 1 kB.
  The packer README gives the firmware limit as **128 kB**, and the bootloader erases sectors 4 and 5 with every
  update.
- **Tested: code past 0x08020000 runs.** A build with only the vector table in sector 4 and `.text` placed at
  0x08020000 (129,224 bytes) flashed, booted, answered, timed as usual (903 µs a pass) and put osc A1 on its table
  at three raw CVs; flashed twice. `link.ld` is now 128K, the packer's limit; the normal build is byte-identical
  to the 64K one, since nothing is placed past its end yet. Unused space: `.ARM.exidx`/`.ARM.extab` (about 1.7 kB of
  unwind tables) would go with `-fno-unwind-tables -fno-asynchronous-unwind-tables`.
- **A boot with the DAC80501 wrong.** The first boot of 0.8.4 (after that test image) played a low rumble: middle
  C at 25 Hz, osc A1 at 4.92 kHz whatever its CV (0x8000, 0xA000, 0xC000), the filters nowhere near their tables,
  while the VCAs (the MCU's own DAC) obeyed. The tuning tables were intact. Reflashing 0.8.3, then 0.8.4, the
  test image and 0.8.4 again all came up right, so it didn't come back in four boots. Everything the DAC80501
  drives (oscillators and filters) was off and nothing else: most likely one of `apply()`'s setup frames (soft
  reset, config, gain = reference / 2 × 2) was garbled on a bus that has little margin (good to 0.6 MHz, run at
  0.5), leaving the DAC at another gain; a pinned 4.92 kHz fits an output clipped at its rail. The DAC can't be
  read back, so the fix would be re-sending the config and gain frames regularly at a point in the S&H cycle where
  no channel samples the DAC80501, so a bad boot heals itself. Done in 0.8.5 (below).

## The DAC80501 setup sent again every tenth of a second (0.8.5, 2026-10-29)

- `sh_refresh_dac_setup()`: every 128th main-loop pass (about 8 times a second, both in the engine and in direct
  mode) it holds every S&H channel, then sends the config and gain frames again (`dac16_setup()`, the same two
  `apply()` sends at start-up). With every 4051 off, nothing samples the DAC while those frames clock in, even a
  garbled one, and each channel's own data frame sets the output again before it samples. It costs two 48 µs
  frames on one pass in 128: timing unchanged (904 µs a pass on average, the longest in 2 s 2,729 µs).
- Probes: `6E <0|1>` turns the re-send off and on (on again at reboot), `6F <reg> <v2> <v1> <v0>` writes a
  DAC80501 register. `pro800 dac-heal` uses them: osc A1 at CV 0x8000 read 342.8 Hz; with the re-send off and the
  gain at reference / 2 × 1 it read 119.6 Hz and stayed there; 0.3 s after the re-send went back on, 342.8 Hz.
- The raw CVs and `check` read as on 0.8.4 (oscillators +0 to +6 cents, filters -40 to -44 at note 48).

## Envelope modes, Macro 1 on M TUNE, a noise LFO, step recording (0.9.0, 2026-10-29)

- **Envelopes** (per envelope and layer, stored at record bytes 74-95, which were reserved, so older patches read as
  before): a key-up trigger, DAHDSR, and a curve. Key up: `gate_off()` starts the envelope (`ENV_DELAY`) instead of
  releasing it, and marks the voice fresh when it's the amp envelope so its pitch goes out before the VCA opens;
  `synth_all_off()` (patch loads, mode changes) only releases. In `ENV_DECAY` a key-up envelope counts its decay time
  and then releases, so DEC is how long it stays near the sustain level. `gate_on()` releases a key-up envelope
  that's still running. DAHDSR: `ENV_DELAY` before every attack (zero delay goes straight through in the same
  update), during which the level falls at the release rate, so a stolen voice doesn't click; the hold reuses
  cycling's `ENV_HOLD`. Delay and hold are 0-99 on the attack's knob curve (0-10 s). Cycling wins over both (an
  envelope that cycles starts at key down). Changing the trigger while a voice sounds restarts (key held, normal
  trigger) or releases that envelope, so none is left at its sustain level without a key.
- Curves: analog (as before), linear (rates are full scale per second; the routes' time multipliers apply as to the
  exponential rates), swell (the attack grows ×2^5 from 1/31 below zero, reaching 1 in its time; decay and release
  as analog). Synced cycling takes the same shapes from its phase.
- Panel: Settings 6 became the envelope page: `En.x` modes `AdSr dAHd UP.AS UP.dA CYCL <divisions>`; Arp Up-Dn
  flips to the curve (`CU.x`: `AnLG LIn SUEL`). While the page is open the four ATT / DEC knobs nudge the page's
  envelope's delay and hold, relative like Shift's secondaries, and the engine keeps them frozen
  (`synth_freeze_pots()`, a per-pot version of MOD ASSIGN's freeze). Host settings 44-65 (the read-only ones moved to
  66-69); the 54 reply is now 151 bytes, so `SYSEX_MAX` went from 120 to 160.
- **M TUNE is Macro 1's knob:** its parameter (still `POT_MASTER_TUNE`, per layer, stored, in pickup) is read as
  −1..1 with a 2 % dead zone in the middle and added to Macro 1 with the offset and the routes; MOD ASSIGN on it
  picks Macro 1. Master tune is gone (every preset had it centred, so they sound the same; the presets' key is
  `macro1` now, as the tool's).
- **Noise LFO shape** (`nOIS`, shape 8): 16 random values per LFO cycle, linearly glided, beside the phase (the
  grid-locked LFOs set the phase directly, so the noise has its own counter). At the top rate (51 Hz) that's ~820
  values a second, close to the tick rate.
- **Voices per layer:** `layer_voices` (record byte 37, 0 = the old automatic 4 + 4 / 7 + 1) fixes layer 1's
  count in split, stack and channel mode (1-7, the rest layer 2's); `voice_split()` returns it. Setting it releases
  every voice, as the other changes that move the split do. Panel: Settings 3, Arp Up-Dn (`Lr.Uo`: `AUto`, `1-7` …
  `7-1`); host setting 66 (the read-only ones moved to 67-70). On the synth: stack at 6 + 2 read back and played;
  the host build gave 2 + 6, 4 + 4 and 6 + 2 voices and a mono layer 2 keeping its first voice.
- **Step recording:** hold Rec, press Seq 1 (Rec's press has already started a save; the combo cancels it and the
  edit layer doesn't change). `seq_record_note()` writes the next step relative to the first note; Seq 2 a rest;
  Rec or Seq 1 ends it; loading a patch ends it. Notes sound through the engine without `seq_key_on()`.
- Build 69,372 bytes: now past sector 4 into sector 5, which 0.8.4 showed runs.
- **On the synth** (flashed, P34): the pass is 946 µs on average with no notes (904 on 0.8.5), the longest in 2 s
  2,769 µs. A key-up amp envelope stayed silent while the key was held and sounded for ~0.6 s after it was let go
  (`record`, 0.1 s windows); DAHDSR with delay 60 (0.37 s) started the note ~0.4 s after note-on. A host build of
  `synth.c` with stubbed drivers (in the session's scratchpad, not kept) stepped linear DAHDSR, swell, key-up, the
  noise LFO and step recording as intended. Not tried on the panel itself: page 6, the ATT / DEC delay and hold, and
  Rec + Seq 1.
- **The oscillator waveforms, measured** (osc A alone at C3, filter open, recorded through the Move, one cycle
  averaged over 0.6 s): the output is a falling saw, so the chain inverts (the synth or the Move's input; the web
  page draws it as recorded). Against a rising saw, the triangle is lowest at the saw's reset and the pulse is high
  from the reset for 0.048 + 1.034 × PW of the cycle (0.15 at PW 10, 0.57 at 50, 0.98 at 90; silent at 95). Levels:
  triangle 1.37 × the saw's peak-to-peak; the pulse's steps 0.83 × the saw's reset. The waves simply add: saw + tri
  and tri + square fit weighted sums of the solo recordings at equal weights to 0.3 % of the energy.
- Saw + square looked odd at first (a fit gave the pulse at −0.23 of its level with the width flipped), so a PW sweep
  (5-95, solo square and saw + square at each): the mix has two upward steps per cycle (output frame), 0.815 and
  0.185 of the saw's reset, d apart. A saw plus a full pulse of width d is exactly that, shifted in time: its reset
  is the small step (the pulse falls there) and the pulse edge the big one. A time shift can't be seen or heard in
  a periodic wave, so the "weak flipped pulse" and the plain sum are the same waveform; the plain sum is the
  physical reading. The mix levels across the sweep (−0.2 to −2.8 dB against the saw alone) match it within 0.5 dB.
  The web page's picture (before: equal-level waves averaged, a rising saw, the pulse width scaled 0.05-0.95; its
  phases were right) now adds the waves at the measured levels and widths, upside down as recorded.

## Envelope trigger and timing, the mod matrix in stages, MIDI channel, sounds as JSON (0.9.1, 2026-10-29)

- **Trigger as its own switch, and cycle counts:** the key-up trigger is a bit per envelope that
  applies in every mode, cycling included (`key_up_envs()` no longer masks out cycling envelopes; `gate_off()` also
  resets a key-up envelope's phase and cycle count). A cycling envelope runs `env_count` cycles (record bytes
  336-343, 0 = endless, 1-99): `env_t.cycles` counts each fall's end (free) or each phase wrap (synced); when it
  reaches the count, `cycles_done()` leaves a key-down envelope at rest (`ENV_REST`, level 0) until the key is let go
  and ends a key-up one (`ENV_IDLE`). Record byte 344 = 1 marks records that store the trigger this way; at load an
  older record's trigger bits are cleared for its cycling envelopes, since cycling used to win. Panel: page 6 lost
  `UP.AS` / `UP.dA` (`AdSr dAHd CYCL <divisions>`); Arp Up-Dn steps through mode, trigger (`tr.x`: `dn UP`), curve
  (`CU.x`) and count (`Cn.x`: `LOOP`, 1-99). Host settings 67-74 are the counts (the read-only ones moved to
  75-78); the 54 reply is 167 bytes, so `SYSEX_MAX` went to 176. Host build (real `synth.c` / `lfo.c`, hardware
  stubbed; amp cycling 0 / 10 / 54 / 10 ms, synced at 1/16 and 120 BPM): key down loop 25 peaks in 2 s; key down
  count 3 → 3 then silent while held; key up count 3 → nothing while held, 3 after, then idle; key up loop → keeps
  cycling (the voice stays busy); synced count 4 → 4 both ways, key up then idle.
  On the synth (installed; amp cycling synced at 1/8, key up, count 3, the clock at 135 BPM; `record`, 0.1 s
  windows): silent through the 1 s hold, then about 0.7 s of sound with two dips (3 × 0.222 s), then silence.
- **Stage times in note values:** `env_tempo`, a bit per envelope and layer (record bytes 345-346,
  host settings 75-76, the read-only ones now 77-80; the 54 reply is 171 bytes). `env_rates()` takes each time
  (attack, decay, release, hold and rest while cycling, DAHDSR's delay and hold) as a step: knob × 15 rounded, 0 =
  no time, else the LFO division table backwards (1 = 1/32 … 15 = 8 bars) × `lfo_quarter_seconds()`, worked out
  every pass, so tempo changes follow. Not for synced cycling (its shares rule). `knob_steps()` makes the ADSR knobs
  stepped (hysteresis, snapped to the step) while the envelope they edit on the edit layer takes note values,
  sustain only while it cycles. Panel: page 6 got `tI.x` (`SEC`, `SYnC`) after the trigger; turning a knob shows the
  note name (`0`, `1-32` … `8bAr`), and so do the delay and hold.
  Host build (120 BPM, no clock): attack 1/8 peaked at 0.249 s, decay 1/4 reached -60 dB 0.501 s later; a knob 1 %
  off its step snapped to it. On the synth (MIDI clock source with no clock running, so 120 BPM): amp cycling in note
  values, hold 1/8, fall 0, rest 1/8, gave ~0.25 s pulses every 0.5 s.
- The 54 reply's read-only values (env select, preset mode, patch, morph) are always its last four, but they move
  whenever a setting is added: a web page from before an update showed "manual" and the wrong patch. The web page and
  the tool now take those four from the end of the reply, and the settable ones from the start.
- **The web page with older firmware** (back to the release 0.9.0, the first public one): builds keep the release's
  version number, so the page tells them apart by the 54 reply's length instead (71 settings in the release, 81
  now; settings are only ever appended). `FEATURES` names each later feature by its first setting; `feature()` is
  true when the reply reaches it (or nothing is connected). Controls hide when their feature is missing, the
  release's key-up trigger is drawn and offered only while not cycling (cycling won there), `data-needs` /
  `data-unless` swap the cheat sheet's lines, and the status line lists what's missing. Checked in the built-in
  browser with fake 71- and 81-setting replies (the counts compiled from the enum at v0.9.0 and at HEAD).
- **FIX → VCA did nothing:** into the VCA and the volume a 0..1 source counts down from full, `(source − 1) × depth`,
  and FIX is always 1, so every depth gave 0. FIX there now turns the level down by |depth| (either sign), the way
  to set one layer's level against the other's. On the synth (P24, layer 1, velocity 127): no route −24.8 dBFS,
  ±50 −31.3 / −31.5, 99 at the noise floor. The web page's modulation arc now shows 0..1 sources into VOL (and FIX)
  turning it down.
- **Sounds as JSON on the web page** (Patches tab): the preset files' format (`presets/README.md`), so the 64 presets
  load in the browser and a downloaded sound installs with `install.sh`. `readSound()` reads the settings (54), both
  layers' parameters (53), the buttons (4C), the four step patterns (4D), both layers' routes (57) and the sequence
  (48 / 46); `planSound()` turns JSON into SysEx after the init sound (5F), as `plan.py` does, checking every name
  and range first, and leaves out settings the firmware hasn't got (with a note). Saving renames the slot (5D, 59)
  so the JSON's name survives. `plan.py` gained the 0.9.0 settings and the noise shape, and its `seq set` passes the
  tool's `updown`. Sequence BPM is 30-255: the firmware keeps it in a byte (`sequence_t.bpm`), so the tool's
  `seq set bpm=300` wraps. Tested in Node with the page's own code against a fake synth answering the same SysEx: all
  64 presets load, read back, load again to the same state and match the files (knobs on the 0-99 grid, lanes ±1:
  they're 63 steps a side), and `plan.py` accepts all 64 read-backs; bad names and ranges are refused before anything
  is sent. In the built-in browser (fake synth): Load and Show through the buttons. Not run against the synth itself:
  WebMIDI isn't in the built-in browser.
- **Patches tab fixes:** Import took only 0.7.x's 256-byte "P8P1" files while Export has written 768-byte "P8P2"
  records since 0.8.0, so no exported patch could go back. It now takes a 768-byte P8P2 record whose patch half
  passes store.c's checksum (0-507 at 508), says a P8P1 file is from 0.7.x, and writes nothing otherwise. "Back up
  everything" read 400 records (past the patch area into the factory bank and the tables); it reads the 128 now
  (98,304 bytes). The tool's `seq set bpm` is 30-255 like the JSON. Tested in Node with the page's handlers against
  a fake EEPROM answering 79 / 5A: a good record lands byte for byte, damaged, P8P1 and short files leave the slot
  alone, and the backup is 2,048 reads ending at 0x18000; the JS checksum matches store.c's on generated records.
- **MIDI channel** (system-wide, Shift + NOISE `CHnL`, setting 77, `pro800 set midi-channel all|1-16`, the web
  page by CC A / B): 0 every channel (as before), else the channel messages (notes, CCs, bend, pressure, program
  change) on other channels are dropped in `handle_midi`, before anything reads them; clock and SysEx pass. A heard
  message's channel is rewritten relative to the setting, so the per-channel layer modes count from it: channel
  mode N / N + 1, a voice per channel N to N + 7, MPE N (master) to N + 8. Kept in the global block's byte 10 (inside
  its checksum; 0 in every older block, so it reads as every channel). Changing it lets every voice go, since a key
  held on a channel no longer heard would never be let go. The read-only settings move to 78-81 (82 in all, 175 of
  the 176-byte SysEx buffer). The tool's `play`, `cc` and `bend` send on the synth's channel when one is set; the
  web page's Play tab follows it. On the synth (CC 5 = glide, read back with `params`; the Move's audio input gave no
  samples that day): every channel took channel 6; set to 3, channel 6 was ignored and 3 heard, and `cc` without
  `--channel` used 3; channel mode from 3: 3 → layer 1, 4 → layer 2, 5 and 2 ignored; the global block read 03 at
  byte 10, 00 after setting it back.
- **Flashing with the web editor open fails.** The bootloader's port is called "PRO 800" too, so a page left open
  in Chrome reconnects and polls it (54, every 0.9 s) in the middle of the update: the header never gets its `03 31`
  and every block after it answers `01 00 07`, the image is rejected (`noFW` at power-up) and the synth waits in
  `boot`. A power-cycle doesn't help while the page polls; closing it does (then `03 31`, and `01 00 00` at the end).
  Neither the file nor the size was at fault: the previous build failed the same way.
- **Arp Assign held on a layer without routes** (issue 5: "Arp Assign shows None instead of SrC"): a press is
  short only if it's let go within 0.6 s; longer, it browses the routes, and with none `browse_begin` showed `nonE`
  for a second and left, so a slightly long press looked like the button not working (same in the 0.9.0 release).
  With no routes to browse, a hold now starts MOD ASSIGN as a press does (`assign_start()`: `SrC`, the knob touched
  last offered as the destination).
- **Panel fixes from testing that** (reported on the synth): leaving MOD ASSIGN or the browse left its last text
  (`SrC`, a route's part) on the display, since `assign_end()` cleared the timer but never redrew; it calls
  `idle_display()` now (the patch number). Shift + Perf after PERF's init sound brought the sound back but not the
  step sequence (`init_patch()` clears it, and undo kept only a `patch_t`), nor the morph target, the name or, after
  a load, the slot: undo now keeps all of that (`undo_state_t`, 2 × ~1 kB static) and swaps it, so pressing again
  redoes. VALUE without detents gives 4 transitions per cycle; browsing and scrolling sources and destinations moved
  a step per 2, twice as fast as patches and menus, now per 4 (the depth stays at 2). Browsing shows the route's
  number while VALUE turns (`nod1` … `nod9`, `no10` … `no16`) and, 0.8 s after it rests, cycles number, source,
  destination and depth, 0.6 s each.
- **MOD ASSIGN in three explicit stages** (reported on the synth: a second VALUE click while picking the source
  landed in the destinations, which then switched to the route's depth after 0.7 s, so it read as the depth stage).
  VALUE scrolled sources only while none was set, so the first click set one and every later click scrolled
  destinations; and a number key dropped the destination offered from the last knob. Now `assign_source_taken`
  separates scrolling from taking: VALUE scrolls sources until one is taken (Arp Assign, a number key, a source knob,
  or touching a destination), then destinations by name only until one is taken (Arp Assign, a knob or an LFO
  button), then the depth. `assign_take_source()` goes straight to the depth when a destination is offered, which is
  now only a knob touched in the 5 s before Arp Assign (`last_pot_at`), not one touched any time before.
- **P66 sounding wrong after start-up until reloaded** (reported twice: the bass pluck on layer 1 loses its filter
  snap; loading another patch and back fixes it). Not found yet: right after a flash and boot, and after `pro800
  patch load P66`, settings, both layers' parameters, buttons, routes and the sequence read the same. A clock-synced
  LFO with no clock heard runs at 120 BPM (`lfo_update()`), checked on the host, so that isn't it. The lead: layer 1
  is legato, which retriggers the envelopes only when `held_count` is 0, and only a load (`synth_all_off()`) clears
  it, so a key the engine still thinks is held would do exactly this. The new read-only probe (SysEx 55, `pro800
  engine`: held keys, voice gates and envelope stages, keytrack, switch overrides) is for reading that state at the
  next start-up, before anything reloads.
- **MOD ASSIGN with a knob that is a source and a destination at once** (reported: on the init sound, turning an
  ADSR or LFO knob two clicks picked it as the source and then as the destination, straight to the depth, and the
  next Arp Assign left). The turn that picks the source keeps sending; those events now go on being the source
  (`assign_source_pot`), and the knob counts as a destination only after resting a second. A knob offered as the
  destination (touched in the 5 s before) that then picks the source drops the offer.
- **MOD ASSIGN checked against its state diagram** (source → destination → depth, Browse on a hold; each stage
  waits until something is taken, VALUE only scrolls). Two cases didn't match and are fixed: in the source stage a
  knob that isn't a source (CUTOFF) showed `SrC` even with a scrolled source shown, where it should take that source
  and itself as the destination (only knobs that stand for a source pick the source now; the rest go on as
  destinations); and a number key took a destination only scrolled to in the destination stage
  (`assign_take_source()` goes on to the depth only for one taken, or offered before the source).
- **A tap of Arp Assign opened Browse once a layer had routes** (reported). The main loop takes `now` from
  `ms_ticks` before polling the panel, and the press is stamped from `ms_ticks` during the poll (an I2C key read),
  often a tick later; the unsigned `now - assign_press_at >= 600` then wrapped and fired the hold on the press
  itself. With no routes a hold starts MOD ASSIGN like a tap, so it went unnoticed. Sync Clock's hold (restart the
  bar) had the same check. Both compare signed now.
- **An empty slot loads the init sound** (`load_patch()`: VALUE, number keys, program change, the host's 5C), in
  that slot, so REC saves it there; it used to show the number and keep the sound. A damaged slot still shows `nonE`.
  From 0.9.2 the display keeps the slot's number (`P 67`), as for any patch: `init_patch()`'s `InIt` is cancelled there, and
  stays for PERF (asked for after scrolling through empty slots showed `InIt` on each).

## Oscillator tuning modes, the preset bank from the synth, filling the patch memory (0.9.2, 2026-10-29)

- **Filling the patch memory from the web page** (asked for after a forum user had no patches after installing):
  coming from stock, the slots hold stock-format data and read as damaged, and only macOS had a bulk route
  (`pro800 format`, `presets/install.sh`). The Patches tab now formats the memory (5B), copies the whole factory
  bank into the empty slots in order (51; a slot that turns out full moves on with the same sound, a non-stock
  sound is skipped without using a slot), and installs a preset bank: every preset checked by `planSound()` first,
  then each built from the init sound, saved (5D) and named (59). The bank is `presets.json` next to the page
  (`presets/lib/bundle.py`, from web.sh, pages.sh and build.sh, which also writes the release asset) or a file
  opened or pasted. Empty slots are hidden by default. Tested in the built-in browser with a fake synth answering
  the SysEx: 94 factory sounds into the 94 empty slots of a fresh-install memory, format, 95 into 128 (sounds 95-99
  set up as non-stock and skipped), all 64 presets over factory copies (64 saves and names, 64 distinct sounds,
  6,094 messages), a two-preset bank pasted in the JSON box, and a bank with a typo refused with nothing written.
- **The preset bank is what's on the author's synth** (asked for): every patch read with the web page's own
  `readSound()` against the synth, through a new `pro800 sysex` (requests on stdin, replies on stdout, one MIDI
  connection), loading each slot and then the current one back (it had no unsaved edits; the script rebuilds them
  if there are). `presets/*.json` were rewritten from those reads: each keeps its notes, group and key order, and
  lists what it listed plus whatever differs from the init sound (read from the synth too). 40 of 64 had changed:
  edits on the synth (P2, P5, P11, P25, P26, P34, P37, P60, P62 most), and ±1 rounding from the tool's install
  (`amp-rel 44` read back as 45, lanes ±1). P65 "Pad + Arp!" and P66 "BsSQ/LD.Split 1" are new (group `user`).
  Checked: each file on top of the init sound matches its read exactly, and `plan.py` takes all 66.
- **Oscillator tuning modes (PR 6, by planist79):** FREQ A and B each have a mode per layer, semitones (as before),
  octave (±3), free (±36 continuous) or fixed (C1-C8 whatever key is played; bend, spread, drift and pitch routes
  still apply, the scale snaps around the fixed pitch). Stored at record bytes 38-39, 0 = semitones, so older patches
  are unchanged. Hold Seq 1 / Seq 2 and turn FREQ A / B to step the edit layer's mode (a quarter turn a step; the knob
  frozen meanwhile, in pickup after). Host settings 78-81 after the MIDI channel (read-only 82-85, `SYSEX_MAX` 184),
  the tool's `set osc-tune <layer> a|b <mode>`, the page's menus (shown when the firmware has them) and JSON names
  `osc-tune 1 a` (`plan.py` too). Rebased from the 0.9.0-era PR onto main, with fixes: it didn't compile (a sign
  compare), its settings 67-70 collided with the cycle counts (and its tool cases with the read-only ones), octave
  mode moved semitones instead of octaves (`synth_knob_tune_value()` didn't ×12), the display names had M and X,
  which the font hasn't got (now `SEnI OCt FrEE FIHd`), and the envelope page's refreeze dropped the gesture's knobs.
  Tested: on the host, the pitch each oscillator is sent (semitones and free follow the note; octave +1 = +12, −3 =
  C1 from C4; fixed 66 for C3-C6, C1-C8 across the knob, +2 with full bend); on the synth, the four settings, saving
  and loading them in P128 (bytes 07 02), P1 reading semitones, and the JSON read and plan; by the user on the
  panel: the Seq + FREQ gesture steps the modes, and they sound right.

## Osc B's square bleeding through with every waveform off (0.9.3, 2026-10-30)

- **Issue 7** (init sound, osc A down, osc B at 50 with all three waveforms off: "a thin waveform"). The square has
  no on/off line; it's switched off by pushing the pulse width to an end. Osc A went to the maximum, osc B to the
  minimum (p600fw's rule: with B's pulse held high, poly-mod from B detunes A). Measured in direct mode through the
  comparator (osc B alone at 334 Hz, volume B full, saw and triangle off): at PW 0 it caught 334 Hz now and then,
  from 0x0080 up always, so the minimum is a pulse on the edge of vanishing, not silence (the 0.048 + 1.034 × PW
  duty measured on osc A gives ~5 %); from about 0xE800 to 0xFFFF the fundamental was gone.
- **p600fw's rule holds on this board.** Osc A1 at 342 Hz (saw), osc B's waves off, poly-mod gate FREQ A: amount 0
  read the same at either end of PW B; with PW B at 0xFFFF, amount 0x4000 read 363 Hz (+103 cents), 0x8000 398
  (+260), full 475 (+566); at PW B 0 it stayed within 3 cents. Through the filter gate (filter 1 self-oscillating
  at 1666 Hz, amount 0x8000): 2564 Hz (+746 cents) with PW B high, 1668 with it low.
- **The fix** (`synth_update()`): osc B's square off now goes to the maximum too, unless poly-mod from B reaches
  something: the amount's CV non-zero and FREQ A or FILTER on. Then the minimum, as before, and the thin pulse stays
  (in the manual's known limitations). Autotune keeps PW B at 0 (master and poly-mod off there, and the tables were
  measured that way). On the synth (P128, the init sound, A3 held, the comparator reading the voice in normal
  mode): osc B with its waves off gave no pitch, its saw and its square 220 Hz; with osc A's saw and B's waves off,
  poly-mod from B at 25 and 50 into FREQ A, at 50 into the filter, and at 50 with both off, osc A stayed at
  219.7-220.1 Hz. Heard by the user on the synth: osc B silent with all its waveforms off.

## The pulse width knob scaled to 5-95 % (0.9.3, 2026-10-30)

- **Reported on the synth:** the square goes silent above PW 92. That's the duty measured through the Move in 0.9.0
  (the web page's waveform picture): high for 0.048 + 1.034 × knob of the cycle, so 100 % at 0.92, a square at
  about 44 and 57 % at 50. In CV (`0.05 + 0.9 × knob` before) that's 1.149 × CV − 0.0094.
- **Every oscillator's top edge** (direct mode, each oscillator alone at 0x8000, square only, the comparator against
  its pitch at PW 0x8000, three readings within 1 %): A1-A5, A7, A8 and B1-B5, B7, B8 clean to 0xE000 (0.875) and
  lost by 0xE400 (0.891); A6 and B6 clean to 0xDC00 (0.859), lost by 0xE000. (`pw_edge.sh` in the session's
  scratchpad; not kept.)
- **Now** (`PW_CV_BOTTOM`, `PW_CV_RANGE` in `synth.c`): CV = 0.0517 + 0.7833 × knob, so 0 = 5 %, 50 = a square,
  99 = 95 % (CV 0.835, under voice 6's edge). The bottom is where it was; patches move a little: 50 from 57 % to a
  square, 32 from 38 to about 30 %, 92-99 from silence to a thin pulse. LFO → PW (±0.45 in CV) and routes (MOD_PW,
  in knob units) are unchanged, so modulation still reaches silence at either end. The web page draws the new duty
  for firmware 0.9.3 and later (nothing in the settings marks it), the old one before. On the synth (P128, the init
  sound, square only, A3 held): PW 0, 50, 92 and 99 all read 218.7-220.6 Hz on both oscillators; 92 and 99 were
  silent before.

## Knob pickup off (0.9.3, 2026-10-30)

- **Issue 4** ("Option to turn off 'Knob value catch'"): pickup makes the synth feel less immediate, a knob doing
  nothing until it passes the stored value. A system setting now turns it off: a knob in pickup takes over as soon
  as it moves, jumping the value to its position (the stock firmware's behaviour). Pickup stays the default.
- **How** (`read_panel()`): a knob waiting for its value already noted which side of it it started on, at its first
  free pass; it now notes where it was then too (`pickup_from`). With pickup off it takes over once it's more than
  2 % from there (`JUMP_MOVE`: above the pots' jitter, which the 1.5 % pickup window already lives with), or a step
  for a stepped knob (FREQ A / B, a synced LFO FREQ: they have hysteresis, so any step is a real turn). Reaching or
  sweeping past the value still takes over as before. Changing the setting makes every waiting knob start again from
  where it is (`synth_set_pickup()`), else one that had moved while waiting would jump the moment pickup went off.
- **Per case.** Patch load, switching layer, LFO or envelope (the keytrack switch, Shift's Env 3 / Env 4 on the ADSR
  knobs), the web page or a CC moving a value: with pickup off, a knob that moves takes over at once. The gestures that
  freeze knobs keep their guard: under Shift (the non-ADSR knobs nudge secondaries), in MOD ASSIGN, on the envelope
  page (ATT / DEC set delay / hold) and during Seq + FREQ, a frozen knob doesn't start waiting until the freeze ends,
  so its reference is where it is then. Letting go of Shift, or leaving MOD ASSIGN, moves nothing; moving the knob
  after does. The display's dot still means "not yet taking over" in both modes.
- **Stored** in the global block's byte 11, inside the checksum (`store.c`, `no_pickup`, 1 = off), so older blocks
  (0 there) keep pickup. Host setting 82 after the oscillator tuning modes (1 pickup, 0 jump); the read-only ones
  move to 83-86, so `SET_COUNT` is 87 and the 54 reply 185 bytes: `SYSEX_MAX` 186. Panel: Shift + FIL ENV AMT
  (`P.UP`, `on` / `OFF`), a quarter turn a step, since a full sweep for one on/off step was too far. The tool's
  `set pickup on|off` (and `settings` shows it), the web page's System tab ("Knobs", hidden on firmware without the
  setting through `FEATURES.pickup`) and the cheat sheet's Shift and display lines.
- **Tested** on the host: `synth.c` and `lfo.c` built for the Mac with the panel stubbed, driving pots through
  `synth_update()`: pickup on (stays when turned away, follows once swept past); off (a 1 % wobble stays, 4 %
  jumps, a muxed shared knob jumps on both layers, one semitone step of FREQ A jumps); off with Shift (turned under
  Shift: unchanged; released: unchanged; moved after: jumps), Shift + FIL ATT jumping Env 3 and leaving the filter
  attack alone at release, MOD ASSIGN and Seq + FREQ freezes the same way, the edit-layer switch (layer 2 jumps,
  layer 1 kept), a patch load, turning pickup off while a moved knob waits (nothing moves; it jumps when moved
  after), and back on. The web page in the built-in browser with a fake synth answering 08, 54
  and 7D: the select shows the setting, Off sends `7D 52 00 00` and reads back; with an 86-setting reply (0.9.2) the
  section and the cheat-sheet lines hide, the status names it missing, and the read-only settings still land right.
  The guide's Figure 13 has a "Pickup off (jump)" box, checked in the built-in browser. Builds at 73,760 bytes (with issue 7).
- **On the synth** (2026-10-30, the unsaved init sound; P128 holds a patch): the setting reads back after the flash as
  on, the rest of the settings unchanged. With pickup off, 30 s with no hand on the panel moved nothing, including knobs
  put in pickup from the host (pot jitter at most 1/99, below `JUMP_MOVE`). CUTOFF waited at 99 with the knob at 76: a
  1-point move stayed, further on it jumped to 71 and followed. Shift + FIL ENV AMT toggled it, read back by the tool.
  `set pickup off` / `on` flips byte 11 and the checksum of the global block, and back on the block is identical.

## Backlog

- `pro800 batch` (commands from stdin on one MIDI connection) for the preset installer; a web button to install the bank.
- Audition the preset bank on the synth and tune levels and cutoffs.

Nothing here is needed for the synth to work; this is what's open, consolidated 2026-10-28 after the review, the
0.7.7 / 0.7.8 measurements and the dac-test sweep. What each buys is in its line.

**Timing and engine**

- **The bus is closed** at 0.5 MHz: everything above 0.6 MHz fails whatever drives it (the sweep); a scope on
  SCLK / SDIN at U46 is the only way to learn why, and the 2 kHz every-CV refresh plan waits on that. 154/154
  cycles (0.55 MHz) would take 2 µs off each frame but sits close to the measured edge.
- **Port and commit the Mac simulations.** The 0.7.7 S&H model (`sim.c`: GPIO B / D pins, the DAC80501 frame, both
  on-chip DACs, in cycles, checking every sample) and the 0.7.5 engine harness (`test.c`: the real `synth.c`,
  `lfo.c`, `seq.c` with the hardware stubbed, clock and channel-mode scenarios) were left in another session's
  scratchpad and target older interfaces (`synth_envelopes()`, `GPIO_BSRR` as an lvalue). Ported under `tools/sim/`
  they would rerun for the next change to `sh.c` or the ticks.

**Hardware drivers**

- **Hardware I2C1 for the panel** (PB6 / PB7) with interrupts, or at least non-blocking writes: a digit change
  still stalls the loop 0.8 ms, a full redraw 3.5.
- **Hardware SPI4 for the EEPROM** (PE2 / PE5 / PE6), or at least dropping the 1 µs half-bit delays: a patch load
  blocks about 25 ms today. With a fast load, **seamless patch change** (no `synth_all_off()` in
  `synth_apply_patch()`) becomes possible.
- **MIDI THRU** (PA0 / MIDI_SEL on PB4) is mapped but not driven.

**Panel**

- Sequencer transport and step recording from the panel (web-only today).
- Compare / undo: hold Preset to hear the stored patch; make Perf's init a hold or double press.
- Scroll the patch name across the display on load; show the pickup direction (top / bottom segment); a velocity
  and aftertouch amount page in Settings; layer copy (Seq 1 + Seq 2); Tune progress on the display; a panic
  combination; hold a Settings key to reset it.
- Patch names from the keypad (multi-tap); today the web page or `pro800 patch rename`.

**Features**

- MPE: the RPNs for the member bend range and zone layout (fixed at the lower zone, channels 2-9, 48 semitones).
- LFO delay / fade per layer (today per LFO, shared by both layers; the format-2 record has room).
- SYNC IN with a real clock source (2 ppq assumed), and a pulses-per-quarter setting if it isn't.
- Shift + Arp Up-Dn to swap the two LFOs, Shift + Tune for the last tune's result: not done (LFO 1's routing
  lives in the panel switches, so a swap isn't symmetric; the tune result isn't kept).
- A converter for the 0.7.x patch records (the backups), if the old sounds are wanted back.
- Voice stealing: a one-tick VCA dip for a stolen voice was considered and rejected (a step of its own); the pitch
  jump with the VCA open sounds like legato.

**Not possible from firmware**

- Per-waveform levels within an oscillator: the waveform switches are on/off logic lines to the voice board.
  Workaround: the second waveform on the other oscillator, with its level.

**Nice to have**

- **Virtual PRO-800 on the Mac.** Not chip emulation: QEMU's STM32F4 has no USB OTG, and Renode would need models
  of the whole board (bit-banged DAC, S&H muxes, HT16K33, pot muxes, EEPROM, comparator) and still says nothing
  about timing. Instead:
  1. Build the firmware for macOS with only the drivers swapped (the Mac tests already stub the engine's): USB →
     a CoreMIDI virtual port named "PRO 800" (the web page and `build/pro800` work unchanged), EEPROM → a 128 kB
     file seeded from a backup, DAC / S&H → a timestamped CV log per channel, panel → scripts or the web page,
     the main loop on a timer. Catches SysEx, layout, storage and modulation bugs (it would have caught the stale
     tuning tables if the tables' format were part of it); about two days. Code in `main.c` that touches
     registers directly has to move behind the drivers first.
  2. Then the circuitry as behavioural models fed by those CVs, out through CoreAudio: S&H droop, exponential
     VCOs with the measured per-oscillator offsets, mixer and noise, a 4-pole low-pass with resonance, the VCA
     with its measured 7-9 % dead zone, and the tuning comparator (so `tune.c` runs). Calibrated from our
     measurements and the schematic; good for hearing routes, envelopes and sequences, not for judging tone.
     SPICE on single stages offline at most.
  Stays hardware-only: timing headroom, the bootloader / flash path, the real sound.

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
