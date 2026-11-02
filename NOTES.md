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

SynthTribe 3.2.4's update, tested 2026-10-30 (macOS 26, Chrome closed, synth entered with 0 + 7, so only the
bootloader was running): it never finishes. The bar stays at 0% and the display goes from `boot` to `UPDA`. Recorded with
`pro800 listen`, the bootloader sent the version (`09 00 00 00 00` after an unfinished update, else the last update's
version), then `03 31` twice, 2 s apart, then nothing for over 3 minutes: no `01 00 07`, no `01 00 00`. SynthTribe then
sat idle with no sending thread. Its symbols (`queryDeviceMode`, `currentModeChanged` comparing 0x31, `receivedACK`,
`sendFirmwareData`) suggest `03 31` answers a mode query as well as our header. Unknown: what SynthTribe sends after it,
since CoreMIDI shows only the synth's side. `pro800 flash` with the same file works, and it's the way back to stock.

SynthTribe 2.6.7 on Windows 11, reported by the user 2026-10-30: started after the synth was in the bootloader (0 + 7),
it seems to put stock 1.4.6 back, so the stall above may be specific to 3.2.4 or macOS. Not checked from here with
`pro800 version` (the tool is macOS-only). The portable exe (Enigma Virtual Box, unpacks only small `evb*.tmp` stubs to
Temp) has no installed folder and no PRO-800 update inside: its SysEx headers are all device `1E`, and its cloud URL
is `cloud.midasconsoles.com/api/v2/firmware/…/latest`. After the update no file of that size or with the update's
header was on any drive, INetCache included, so unlike the Mac there's nothing to copy; whether it was never written or
was deleted isn't known. The running process still held it: a read-only search of its memory for `F0 00 20 32 00 01 24`
found one 169,061-byte run of 664 messages whose SHA-256 is `0f7b1af2…5e18`, the same file as the Mac cache
(`STOCK_TESTED` in `tools/web/install.html`).

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
  Neither the file nor the size was at fault: the previous build failed the same way. **From 2026-10-31** `connect()`
  sends only 08 and 79 and stays off anything that doesn't answer 79, the poll is gated while connecting, and the
  reconnect waits 3 s of quiet, reacting only to real port state changes. The remaining risk: a flash from outside this
  browser can get those two messages about 3 s after the bootloader's port appears, then silence, so closing WebMIDI
  pages before a command-line flash still stands. `pro800 install` now waits 8 s after that port appears (`PORT_STEADY`):
  the editor sends at 3 s, and an idle installer tab starts at 3.5 s and sends its last, unanswered, about 3.2 s later. A
  very late browser can still interfere, and plain `flash` has no such wait.
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
  for firmware with the 0.9.3 settings (it tests for the layer links, since nothing marks this itself), the old one before. On the synth (P128, the init
  sound, square only, A3 held): PW 0, 50, 92 and 99 all read 218.7-220.6 Hz on both oscillators; 92 and 99 were
  silent before.

## Layer links (0.9.3, 2026-10-30)

- **What:** `layer_link` (patch record byte 347, host setting 83, `pro800 set link`, JSON `link`), 7 bits: `LINK_ENV(e)`
  (amp, filter, Env 3, Env 4), `LINK_LFO(w)`, `LINK_FILTER` (cutoff, envelope amount). Zero in every older record.
  One byte of the 161 reserved, so 160 are left. Effective only in split, stack and channel (`links()` is 0 when
  `one_layer()`).
- **Resolved at read, not copied:** `eff[]` in `synth_update()` reads knobs through `param_layer()`
  (`link_of_param[]`), morph included; `env_rates()` gets `env_layer()`, `key_up_envs()` merges the bits, LFOs go
  through `lfo_read()` / `lfo_layer()` and `lfos_note_on()`. Layer 2's values stay in `layer_param`, the arrays and the
  record, so saving keeps both and unlinking brings them back. A linked LFO is layer 1's `LFO_ID`, phase included;
  layer 2's own keeps running unheard. "First key" counts both layers when linked, or a key on one layer would restart
  the other's LFO. Per-voice phases are per voice index, and the layers' voices don't overlap.
- **Host stays raw, panel redirects:** 52 / 7D / 53 / 54 read and write each layer's own values, so a JSON read-back and
  `plan.py` round-trip whatever the order. The panel's knobs, the Settings envelope pages, LFO Shape and LFO 2's routing
  go to layer 1 (`synth_env_edit_layer()`, `synth_lfo_edit_layer()`), and `synth_pot_linked()` lights both layer LEDs for
  a second. The web page keeps `rawSettings` and shows `settings = linkedView(raw)`; `linkedWrites()` splits a write
  (bits of linked envelopes to layer 1, the rest to layer 2), `paramLayer()` and `stepsLayer()` send to layer 1, and
  `refreshParams()` overlays layer 1's knobs. JSON uses `rawSettings`.
- **Not linked:** routes (per layer on purpose), PMOD FIL ENV, glide, tuning, macros. Sync, retrigger, delay and fade
  are per LFO for both layers already. No panel page sets links yet; the web page and the tool do.
- **Tested on the synth** (P128, split at 60, note 72 on layer 2): self-oscillating filter with cutoff 60 on layer 1 and
  45 on layer 2: unlinked 753 Hz, linked 2173 Hz, layer 1 to 50 gave 1075, unlinked again 757. Layer 1's amp key-up:
  unlinked 523 Hz while held, linked no signal. LFO 1 and LFO 2 (with its destinations) on layer 1 only, square: 524 / 523
  Hz unlinked, about 676 Hz (+440 cents) linked. Saved `amp-env,lfo1,filter` to P128, reloaded: the link and layer 2's
  own cutoff 45 both came back, P1 reads `none`. In the built-in browser with a fake synth: chips only in two-layer
  modes, linked knobs show and write layer 1, a `cycle2` change splits into `cycle2` and `cycle1`, delays, shapes and step
  patterns go to layer 1, a JSON read keeps layer 2's own values. **Not tested:** the panel side (a linked knob on layer 2
  editing layer 1, the LEDs), which needs hands on the synth; the two layers' LFO phases side by side (same `LFO_ID` by
  construction).

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
  (0 there) keep pickup. Host setting 82 after the oscillator tuning modes (1 pickup, 0 jump); with the layer links at 83 the read-only
  ones are 84-87, so `SET_COUNT` is 88 and the 54 reply 187 bytes: `SYSEX_MAX` 188. Panel: Shift + FIL ENV AMT
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
- **With the layer links** (merged on `0.9.3`, installed, 75,240 bytes): the settings reply reads right at 88 (pickup 82,
  link 83, read-only 84-87). 15 s of pickup off moved no knob on P128. The filter test still gave 762 Hz unlinked, 2184
  Hz linked, the same with pickup off, 766 Hz unlinked again. A jump now writes to `param_layer()`'s layer, so a linked
  knob on layer 2 jumps layer 1. The web page with a fake synth: writes 82 and 83 land on the right settings; with an 86-
  and an 87-setting reply the missing features are named, the Link buttons hide and the patch number still reads. **Not
  tested:** the panel side of either together (needs hands on the knobs).

## Chords in the sequence (0.9.3, 2026-10-30)

- **What:** a step plays up to three notes. `SEQ_CHORD(flags)` (bits 5-6 of the sequence's flags, so no format change; firmware
  before 0.9.3 ignores them and plays the step's own note) says how many of the lanes, the last ones, are chord notes.
  A value is semitones from the step's note, `SEQ_LANE_ZERO` (64) = none, which is also what every lane holds after
  `seq_clear()`. `sequence.lanes` counts them too, so Seq A-D keep their indexes and the step budget (`seq_max_steps()`)
  needs no change: 48 steps with one extra note, 40 with two. `seq_lane()` gives chord lanes 0, so a route from one is silent.
- **Engine:** `sounding` became a list. `play_note()` builds the notes (clamped, duplicates dropped), turns on those not
  already held by a tie, then turns off the ones no longer wanted, so a tie is legato note by note. Gate, velocity,
  probability and ratchets are the step's. Step recording writes one note and clears the step's chord notes.
- **Host:** `47` / `48` carry the raw `lanes` and flags; `seq set lanes=` means automation lanes and `chord=` the rest, and the tool
  adds them. `seq step … n2+4 n3+7` reads the header to find the lanes. JSON: `chord`, `n2`, `n3`, with `lanes` the automation
  lanes only, so older files mean what they did. On the web page `layoutLanes()` moves chord notes when either count changes,
  since a chord lane's index depends on the automation lanes; the tool doesn't, so set the header before the steps.
- **Web:** Chords menu (hidden without the 0.9.3 settings), Note 2 / Note 3 rows in the step grid (`--seq-rows`), the extra notes
  on the overview, step counts in both menus.
- **Tested on the synth** (P128, init sound; A3 held; 60 BPM, 1/4): 3 voices gated on a three-note step, 2 on a two-note one, 0 on a
  rest, 1 on a plain step. Layer in mono, so the newest note sounds: n3+7 329.5 Hz (expected 329.6), n2+3 261.5 (261.6), n2+12 439.7
  (440), +5 alone 293.5 (293.7). Tie from A C# E into A C# F#: two voices kept, stages untouched, one moved. Two ratchets: the
  chord sounded again on the second hit. Saved and reloaded: header and notes back, P1 unaffected. No gates left after letting go.
  `plan.py` accepts all 66 presets as before. In the built-in browser with a fake synth: the Chords menu writes `lanes` 4 and
  flags with bits 5-6, a change of automation lanes moves the chord notes and keeps the mod values, labels and cells show and
  hide, JSON builds the right header and lanes and refuses `lanes` + `chord` over 4 and an `n3` without chord 2. **Not
  tested:** by ear, and the page against the real synth. An arpeggio with chord notes is by reading the code only.
- **The roll lines up with the steps** (reported): it drew the whole sequence across the full width, label column included, so
  nothing sat over its toggle. It now draws the current page, with `stepSlots()` giving each column's real position (the lane
  editors drew at width / 16 and drifted by the column gaps; they use it too, for drawing and for the pointer). A thin strip
  above it shows the whole sequence when it's longer than a page. Checked in the built-in browser with a fake synth: 16 slots,
  page 2 of a 28-step sequence, a pointer over column 3 of page 2 writes step 19 only, and at phone width 8 slots with no sideways
  scroll. At phone width the grid has always shown 8 columns, so steps 9-16 of a page can't be reached there.
- **Editing on the roll** (asked for): `editRoll()`. A fixed window of three octaves from `rollLow` (`ROLL_SPAN`, ▼ ▲ by an octave,
  `rollFollow()` moves it on a new sequence only if its notes aren't in -12..+24), so rows don't change scale under the pointer,
  with `drawRollKeys()` beside it (offset 0 drawn as C, the Cs labelled in semitones, since offsets are from the held key).
  Notes outside the window leave a mark at its edge. Click: a rest becomes the note; a played step takes a free chord lane, or
  with Chords off its note moves; a full step says so. Drag: pitch, held in the window; moving the step's own note keeps its chord
  notes where they are by rewriting their offsets, and refuses a collision. Right edge (6 px): gate, or past the column a tie,
  rebuilt each move from a snapshot of the page from that step, so dragging back restores what it covered; covered steps take the
  note, velocity and chord, and the chain's ratchets go since one would break the tie (probability stays). Double-click or right-click
  removes; removing a step's own note makes its first chord note the own note. Moving a note sideways to another step isn't
  there. Checked with pointer events in the built-in browser with a fake synth: every case above. **Not tested:** with a real mouse and synth.
- **Duplicate and Halve** (asked for; web page only, `duplicateSeq()`, `halveSeq()`): the step length stays. Duplicate repeats the pattern when the
  steps fit `seqMax()`; Halve keeps the first half as it is, an odd length rounding up. A first version also changed the step length to keep the bar's
  length, with a Double; that wasn't what was asked, and it's gone. Checked in the built-in browser with a fake synth.
- **Per-note holds** (reported: stretching a bass removed the short notes above it). A step had one gate, and a tie held all its
  notes, so the two couldn't live together, and the first stretch copied the note over what it covered. `SEQ_HOLDS(extra)`, bits 5-7
  of the stored `extra` (own note, Note 2, Note 3; firmware before 0.9.3 reads only bits 0-4): the note ignores the gate and carries
  on, staying legato if the next step plays that pitch. `play_note()` keeps a note only if it was held (`sounding_held`), by pitch, so a
  bass can be the step's own note in one step and Note 2 in the next; `release_unheld()` at the gate's end stops the rest. Gate 100 % still
  holds all, ratchets none. Host: `45` / `46` carry them in bits 1-3 of `<on>` (bit 0 on), since `<extra>` is masked to 5 bits there; a rest
  reads 0. Tool `h1-7`, JSON `hold`. Roll: a stretch now joins each step beside its notes (`canJoin()`, `freeLane()`), raises Chords when a step
  has no room and the sequence fits, and otherwise stops before that step; a click adds beside rather than moves, the same way. `chain()`: a note
  held over steps moves and goes as one. In a step beside others the note lasts to the step's end, since they share the gate.
  On the synth (P128, 30 BPM, 2 s a step, A2 held; own note held, Note 2 at +12, +15, +12, gate 50): voice 5 stayed gated through three steps
  without restarting, voices 6, 7 and 8 sounded for the first second of their steps, all off after the third step's gate; saved and reloaded the same.
  In the built-in browser with a fake synth: a bass at step 1 stretched over +12, +15, +19, a rest and to mid step 6 keeps them all and raises
  Chords, dragging back restores, an upper note's own gate changes under the held bass, moving and removing take the whole bass, JSON keeps it. Tried by the user on the roll with the synth: long bass notes under short upper notes work.

## Typing a slot number (0.9.3, 2026-10-30)

- A user reported `donE` with the patch not in the chosen slot. From 0.8.0 (128 slots) a number that could still take
  a digit was only held: Rec dropped it and saved to the slot in use, so P1-P12 went wrong and overwrote that slot.
  P1-P9 couldn't be chosen by key at all, for loading either, and the 3 s pause in the manual never existed.
- Now Rec in a save takes the open number as the slot, the pause ends it (`typed_check()`, a lone `0` just goes), and
  any other button drops it. VALUE still drops it and steps from the slot in use.
- After a Tune from the panel the display goes back to its idle state (it was left blank with a patch loaded).
- Memory never formatted (fresh from stock) had no global block, and only Format wrote one, so a saved or installed
  patch was kept but the last patch and the system settings weren't, and the synth started blank in manual mode.
  Now `store_save_patch()` writes the block when it's missing (panel saves, bank installs, factory copies); no slot
  is erased. Raw EEPROM writes (`0x5A`, `eeprom-write`) don't, so writing the stock image back stays stock.

## One shot LFOs (0.9.3, 2026-10-30)

- **What:** two retrigger types after the four old ones, `LFO_TRIGGER_ONE_SHOT` (4, `1Sht`) and `LFO_TRIGGER_ONE_SHOT_VOICE`
  (5, `1S.Po`), and `lfo_cycles[2]` (1-99), per LFO like the rest of the trigger settings: host settings 84, 85 (read-only
  ones now 86-89, `SET_COUNT` 90, `SYSEX_MAX` 192), patch record bytes 348-349 (0 = 1, so 158 reserved bytes are left),
  JSON `lfo-cycles`, `lfo2-cycles`. Tool: `set lfo-trigger one-shot|one-shot-voice`, `set lfo-cycles <1-99>`.
- **Two types, not a per-voice switch:** retrigger already means "which phase and what restarts it", and `PoLY` is one of its
  values. A switch would mean nothing for the other types. Every key restarts a one shot; a first-key flavour was left out
  (the settings' value table has room for six names).
- **Stopped = phase exactly 1.0** (`advance_shot()`), which `advance()` never leaves, so no flag is needed. Every shape's
  formula is continuous up to 1.0, so the stopped LFO holds its last output instead of jumping to a shape's rest: triangle,
  sine, square, ramp down -1; ramp up +1; steps the last step; S&H the held value; drift the `next` it reached; noise
  frozen where it was (`noise_advance()` is skipped). Restart uses `new_cycle()`, so drift goes on from where it stopped.
  Leaving one shot mode needs no cleanup: `advance()` wraps 1.0 like any other phase.
- **The delay holds the start back** (`phase_t.wait`), set from the delay's time at note-on (`lfo_time()`; 2026-11-01, read live when synced). Otherwise the phase runs
  during the delay (fine for an endless LFO) and a delay longer than the cycles would fade in only the held end. The fade is
  still the age-based gain.
- **Per voice:** `lfo_voice_phases()` replaces the `== PER_VOICE` tests. In `1S.Po` the shared phase is a one shot too,
  restarted by every key, because LFO → PW reads voice -1: free-running it would never stop.
- **Clock:** as every retrigger, rate from the tempo, phase from the key; the grid branch is for `FREE` only. MIDI Start
  zeroes the shared phases, which would have restarted a stopped one shot with no key, so `clock_update()` skips one shots.
- **Links:** nothing special. `lfos_note_on()` and the voice update already go through `lfo_layer()`, so a linked one shot is
  layer 1's: a key on either layer restarts it, and in `1S.Po` a layer 2 voice's phase is layer 1's LFO's `voice[v]`.
- **Older firmware:** `synth_apply_patch()`'s range check turns 4 and 5 into free-running; it doesn't write 348-349 when it saves.
- **Panel:** Settings 2. Arp Up-Dn is taken there (it picks the LFO), so key 2 again toggles `trIG` and `Cn.1` / `Cn.2`.
- **Probe:** `pro800 engine` now prints each LFO's phase (`lfo_probe()`, 40 more bytes in the 55 reply, 93).

## LFO restarts from a sequence's keys (0.9.3, 2026-10-30)

- **What:** `lfo_restart_keys`, host setting 86 (read-only ones now 87-90, `SET_COUNT` 91, `SYSEX_MAX` 193), patch record byte
  350 (0 = steps, what older firmware did; 157 reserved bytes left), `pro800 set lfo-restart steps|keys`, JSON `lfo-restart`,
  a menu in the web page's Sequencer. No panel page: the sequence is edited from the host. A patch setting rather than a
  sequence flag: the flags' 7 bits to the host are nearly used up, and the LFOs are what it changes.
- **Why a setting:** a sequence's notes went through `layer_note_on()`, so each step was a key to the LFOs. That's useful
  (each note's own one shot) and saved patches may rely on it, so Steps stays the default.
- **How:** `keys_restart_lfos(layer)` says a layer's notes come from such a sequence, not who is calling, so it also covers a
  mono layer falling back to a held note in `synth_note_off_layers()`. It silences `lfos_note_on()` and the `lfo_age` reset in
  `gate_on()`, and `seq_key_on()` calls `synth_lfos_key()` before the first step. There each of the layer's voices restarts
  its age, so delay and fade run from the key and a voice a step lands on later isn't still in its delay or long past it.
- **Per-voice modes** restart every voice's phase together (`lfo_key_on()`): steps move between voices, so restarting only
  some would leave the rest wherever they were. Keys under Keys therefore sound like the shared modes.
- **8CH and MPE** go past the sequence (`synth_voice_note_on()`), so the setting doesn't apply there.

## Fewer buttons on the LFO step grid (0.9.3, 2026-10-30)

- The Trance and Euclid buttons are gone from the web page's LFO step grid (clutter: a pattern is quick to click in). A new
  LFO still starts on the trance gate pattern; the sequencer keeps its own Euclid fill.
- The default pattern is now `1 0 1 0 1 1 1 0`, twice (`LFO_DEFAULT_PATTERN` 0x7575, was 0x3535). It applies to the init sound and
  records that never stored a step count; a patch that stored a pattern keeps it, and P62 Trance Gate names its own.

## Mod routes on both layers (0.9.3, 2026-10-30)

- **What:** `route_both[2]`, a bit per route slot: that route also plays on the other layer's voices, in split, stack and channel.
  Patch record bytes 351-354 (zero: as before; 153 reserved bytes left), host settings 87-90 (each layer's slots 0-7 and 8-15,
  as a setting holds 14 bits and the reply 2 bytes each; read-only now 91-94, `SET_COUNT` 95, `SYSEX_MAX` 201), `pro800
  route-both <layer> <source> <dest> on|off`, `routes` marks them, JSON `[source, dest, depth, "both"]`, Both in the web matrix.
- **Why per route:** a Link for the whole matrix would take away layer 2's own routes. One slot and one depth, on the layer it was
  made on. Sources are read per voice, so velocity and the envelopes are that voice's, and an LFO that layer's unless linked.
- **`evaluate_routes()`** runs its own 16, then the other layer's flagged ones; the second pass stops at once when that mask is 0, so
  patches without any cost one test. `fast_routes` and `uses_env34` look at both tables.
- **Single wires:** `shared_mod[]` adds both layers' `last_mod`, so a both-layers route would count twice. `summed_wire()` lets it count
  only on `newest_layer()`, which matches what a wire means (the newest note). Morph, detune and the LFO rates are per layer: not summed.
- **Slots:** `synth_set_route()` clears the bit when a slot empties or fills, so a new route never inherits one. Copying a layer leaves out
  the source's both-layers routes (they already play on the target) and clears the target's bits.
- **Panel:** none. Arp Assign makes one-layer routes and browses the edit layer's own table, so a both-layers route shows only on its layer.
- **Older firmware** ignores 351-354 and plays them as one layer's routes; it clears them when it saves.
- **Web:** the other layer's flagged routes are listed after the layer's own, marked, with their source and destination locked; depth,
  Both and Remove act on the owner. The knobs' rings and the LFO depth bars count them. The macro and lane readouts show own routes only.

## Three reports before the release: sync's bleed, settings on unformatted memory, channel 16 (0.9.3, 2026-10-30)

- **Issue 9, osc A heard with Sync on and every waveform off.** The same family as issue 7, but sync's reset of a pulse held high. The
  comparator can't show it: with osc A's waves off in direct mode it read only noise (about 6.5 kHz) with sync off and on, against osc B's
  217 Hz through its saw. So this is by reasoning, not measured: `GATE_SYNC` now stays off while osc A has no waveform, where sync has
  nothing to do (poly-mod's source is osc B). On the synth (P128, B at 311 Hz, A3): saw, square and triangle each lock to 311 Hz with sync on, 220
  without. Heard by the user on the synth: osc A silent with Sync on and every waveform off.
- **Issue 10, the MIDI channel lost at power-up.** The cause is memory never formatted (fresh from stock), where no setter wrote the global block
  (`if (global.valid)`), for the channel, pickup, clock and CC numbers alike. They now write it, which marks the memory as ours as a saved patch does
  (the earlier section on typing a slot number); `store_set_last()` still doesn't, so only loading patches leaves it stock. Host test (`store.c` on a RAM EEPROM, blank with a stock
  record in slot 0): all four survive `store_init()`, the stock record stays byte for byte, and 7 checks fail on the old code. On the synth, formatted: channel 16 survives
  the reboot of an install. Not tried on real unformatted memory, since that needs the user's patches wiped.
- **Issue 11, no channel 16.** Shift's knobs move a value by turning, 4095 of travel across the range, so a pot whose ends fall short never reaches the far end: 0 to 16 stopped at 15.
  `shift_turn()` now takes 1.15 × the range per sweep, for all of them. By reading the code; the pot's real travel wasn't measured. Tried by the user on the panel: Shift + NOISE reaches 16. With the tool:
  channel 16 is heard and 15 ignored.
- Before the release's install, the sound on the synth (P68) was compared with `params`, `routes`, `seq` and `settings`: the same after the reboot and after a reload.

## The installer page (0.9.3, 2026-10-30)

`tools/web/install.html`, asked for so that installing, initialising and going back to stock need no command line.

- **Interference is the whole risk.** Testing with Chrome and the MIDI patchbay open, the bootloader answered nothing,
  not even the version request, and refused every block with `01 00 07`, from the page and from `pro800 flash` alike.
  Closing them was enough: the next `pro800 flash` got `03 31` and `01 00 00` with no power-cycle, and the bootloader
  answered the version request again. So any program's traffic does it, not only the editor's poll. The page stops at a
  missing `03 31` or any `01 00 0x` error, and treats a port that answers nothing as the bootloader, since sending the
  update header to anything else does nothing. Editor tabs let go over a BroadcastChannel; other software can't be told.
  **The channel's protocol** (2026-10-31, `watchInstaller()` in `sound-plan.js`): `ask`, `busy <tab>` and `idle <tab>`,
  tracked per tab. A plain `busy` or `idle` still works; an editor tab loaded before this change ignores the new form
  until reloaded. A second installer tab stands back, says another one is running and keeps off the port. A tab that
  closes or crashes says nothing, so two unanswered asks count as finished: counted, not timed, because a background
  tab's timers may run once a minute and a running installer still answers each ask at once.
- **What's on the port** without the USB product id, which a page can't see: only our firmware answers 79, only stock
  answers 77 7E 03, anything else counts as the bootloader.
- **The stock file:** Behringer's cloud (`cloud.midasconsoles.com`) wants an authorised token, and the firmware isn't
  ours to host, so the user opens it. The page checks it's an update and names whether it's the tested 1.4.6 by SHA-256.
- **Images:** stock patches survive the first boot of PRO-8000. An image taken after it differs from the stock image only
  from 0x1F500 to 0x1FA4B (tuning tables, 1,166 bytes over stock's scratch data), so the page saves it after flashing and
  before wiping, to a file chosen at Start because a browser opens that dialog only after a click. An image with a
  global block is refused for the way back to stock.
- **Tested on the synth** in a separate Chrome (own profile, MIDI granted over CDP, the save dialog stubbed): reinstall
  through update mode; to stock with the memory emptied, image identical to `pro800 eeprom`'s; stock to PRO-8000 with
  format and bank, 66 presets read back; to stock writing `eeprom-stock.bin` back; PRO-8000 over that real stock
  memory, leaving it alone; the user's image written back, read back identical. Update mode worked from stock each time.
  Against a simulated synth: the 0 + 7 prompt, a refused header, a refused block, retrying. Not tested: Windows, Linux,
  Edge, a stock file other than the 1.4.6 one, and the real save dialog.

## The tool on Windows (`tools/pro800.py`, 2026-10-30)

- **Why Python and WinMM through ctypes:** the machine has Python 3.13 and Node 22 and no compiler. `python-rtmidi` has no
  wheel for 3.13 on Windows and Node's MIDI addons need building too, so ctypes means nothing to install. On macOS and
  Linux the same file uses `python-rtmidi` (not tried on either: the Swift tool stays macOS's tool).
- **Input** arrives as thread messages (`CALLBACK_THREAD`) on a thread of ours, not in a callback, because WinMM's
  callbacks mustn't call WinMM and the buffers have to go back with `midiInAddBuffer`.
- **Windows MIDI Services** (`midisrv`) sits under WinMM here. The synth shows as "PRO 800" both ways; the port opens while
  SynthTribe has it open too, and every program sees every reply. `midiOutLongMsg` is done in 0.2 ms, before the bytes
  are on the wire, and a version request's reply comes back in 1-5 ms. So the pacing has to be ours, as on macOS, and
  closing waits 50 ms. Without the service a port would open only once, hence the one connection per process.
- **The service can lose a port** (2026-10-31): after `update-mode` the bootloader was on the bus (`1397:1260`, usbaudio
  bound, no problem code) but `ports` showed no "PRO 800" for minutes, and a cable replug didn't bring it either. An
  elevated `Restart-Service midisrv` did, at once. So when the USB check finds the synth and the port is missing,
  restart the service (needs admin) before suspecting the synth. **Or keep a dead one** (the same day, 7 h after that
  flash): "PRO 800" was listed and opened without error, the synth was on the bus as `125F` the whole time, but `version`
  got no reply and a 20 s `listen` nothing; a power-cycle of the synth (a fresh enumeration) brought the replies back,
  with the service untouched. So no reply from a port that opens isn't the synth either: re-enumerate first.
- **`install` runs its MIDI steps as child processes**, each finding the ports afresh. The USB check doesn't use MIDI
  (`CM_Get_Device_ID_ListW`, present devices). A long-running process may keep a stale device list across a
  re-enumeration, as CoreMIDI does; this way it doesn't matter whether WinMM does.
- **A shared port shows other programs' replies.** With SynthTribe 2.6.7 open, a version request on stock 1.4.6 also
  brought `78 7E 03 …` and a bare `F0 F7`, and `77 7E 03` was followed by `F0 F7`. With SynthTribe closed each request got
  only its own reply, so those were SynthTribe's. A stray `F0 F7` during a slot request would read as an empty slot, so
  `backup` gives a real reply 100 ms to follow one, and nothing else accepts `F0 F7`. Another version reply nobody on our
  side asked for still came with SynthTribe closed, so something else was on the port that day too.
- **A flash can't tell who else is talking**, and any traffic breaks one (above), so the tool can't check this: quit
  SynthTribe and close WebMIDI pages first. `find_port` takes exact names on Windows, where "2- PRO 800" would be another
  synth that `install` mustn't flash if the first drops off the bus.
- **Tested on the real synth under stock 1.4.6:** `ports`, `version`, `backup` (all 400 slots, 50 s; this synth's stock firmware
  answers `F0 F7` for every one, so a real patch hasn't come through this path), `listen`, and the no-reply path (`settings`
  and `eeprom`, whose 54 and 79 the web pages already send to stock). Seen while the synth went into its bootloader and
  back, by something other than this tool: the USB check finds both 125F and 1260 (`USB\VID_1397&PID_1260\0.1.0`, its MIDI
  interface `…&MI_00`), the bootloader's port is "PRO 800" here too and opens, and the port comes up a little after
  the USB device.
- **Tested without the synth:** `flash` over real WinMM to the Microsoft GS Wavetable Synth: 3000 B/s asked gave 2982, 9000 gave
  8860, gaps never below what was asked. The input thread with WinMM's messages posted to it on the Move's input: short
  messages, SysEx, a broken SysEx dropped with its buffer returned, an empty buffer not returned, and the Move's real active
  sensing. `flash --dry-run` on the published 0.9.3 file: 376 messages, 95,547 bytes, version 0.9.3 in the header. Everything
  else against the simulated synth of `tools/test_pro800.py`, built from `src/main.c`'s replies.
- **`flash` tested on the real synth** (0.9.4, 2026-10-31, synth already in the bootloader): `03 31` after the header,
  `01 00 00` after block 379, the synth came back as `1397:125F` and `version` answered 0.9.4. Two attempts before it
  broke with `midiOutLongMsg failed: Undefined external error. (1)` at blocks 162 and 209, and the second one also showed a
  version reply (`09 00 00 00 00`) after the header that nothing on our side asked for: an editor tab left open in Chrome
  was polling the bootloader's port (the known interference above). So on Windows that interference shows as WinMM
  refusing a send, not only as the bootloader's `01 00 07`. A 20 s `listen` that shows nothing before a flash rules it
  out; after one, the bootloader takes a fresh header without a power-cycle.
- **Not tested on the real synth:** the commands that need our firmware (`settings`, `state`, `engine`, `seq`, `patches`,
  `routes`, `timing`, `tuning`, `check`, `panel`, `eeprom`), anything else that changes the synth, `update-mode` and
  `install`. Nothing on macOS or Linux.

## Boot, PAnL, VALUE through the patches, controllers on the display (0.9.4, 2026-10-30)

From the GitHub issues (#14, #15, #1). VALUE and `PAnL` tried on the synth; the rest through `tools/test_panel.py`.

- **Boot always loads the last patch** (#14). 0.9.3 stayed in manual mode if that's what you left, so after a format
  or a first install the sound came from the knobs and `MANUAL.md`'s "Boot loads the last patch" wasn't true. The mode
  is no longer stored: `store_set_last()` lost its argument and the global block's byte 6 is written as 1, so going
  back to 0.9.3 boots on a patch too. Memory still in stock's format has no global block and starts in manual mode.
- **`PAnL` is manual mode's idle display** (#15), not a one-second message before a blank display that looked switched
  off. #15 asked for it until the first edit; tried on the synth, the blank after that first value looked just as off,
  so a value now gives way to `PAnL` as it gives way to the slot number in preset mode.
- **VALUE through the patches accelerates** (#1): a cycle moves 8 patches under 30 ms since the last, 4 under 60, 2
  under 120, else 1. The thresholds are a first guess; tune them on the synth. **The load now waits for the turn to rest**
  (`TURNED_REST_MS`, 80 ms, or the next control event): a load blocks about 25 ms and the encoder is polled in the
  same loop, so loading every step lost transitions on a fast turn, which is likely most of why it felt slow. Picking a
  slot in a save accelerates too; it never loaded.
- **Controllers show on the display** as their knob does (#1, `pot_value_text()` shared with the knobs; LFO 2 and Env
  3 / 4 as 0-99). The main loop shows the latest at most 20 times a second, since a controller can stream and a redraw
  stalls the loop up to 3.5 ms, and not while Settings, a save, a recording, MOD ASSIGN, Browse, compare or a typed
  number has the display. A filter-envelope controller formats by the selected envelope's timing (display only).
- **CC 79 is cutoff now.** `cc_map` and `MANUAL.md` said CC 74, but `control_change()` took 74 as Timbre first, so it
  never reached cutoff; MPE needs 74 as Timbre. The CC A / B check moved ahead of it, so CC A or B set to 74 works as
  that source as well (its `cc != 74` test could never run before).
- **Restore everything** on the editor's Patches tab (reported by a user): Back up everything has written a 98,304-byte
  `.bin` since 0.8.0, and nothing took it back; the installer wants a 131,072-byte image and just called it wrong, now
  it names the file and sends you to the editor. The page checks the size and every record's checksum, then writes only
  the slots that differ and reads each back, so a retry picks up where it stopped. Raw writes don't write the global
  block, so memory holding stock data asks for a format first. The old gate tested kind 2, which `store_slot_kind()`
  never returns, so it never fired; now kind 3 with no kind 1 (`needsFormatFirst()`, 2026-10-31). On empty memory never
  formatted the restore goes through and `markMemoryOurs()` marks the block at the end by toggling pickup there and back,
  then a 79 read flushes it; `store_set_last()` doesn't help on an invalid block. It uses the same 79 / 5A SysEx as Export
  and Import, so it works with older firmware too. Tested in Node with the page's handlers against a simulated EEPROM holding a real
  backup (differing slots, identical memory, wrong size, damaged record, no patches, stock memory, declining, a failed
  write and its retry, a failed read-back); not yet run in a browser against the synth.

## The firmware on a computer: panel tests (`tools/test_panel.py`, 0.9.4, 2026-10-31)

- **How:** the real `main.c`, `synth.c`, `lfo.c`, `seq.c`, `store.c`, `display.c` and `controls.c` built for the host with zig cc, no
  source changed. `sim_regs.h` is force-included and redefines `REG()` after `regs.h`, so every register access goes to
  `sim_reg()`. Writing the watchdog is the top of a pass: the firmware runs in a fiber (Windows) or ucontext and comes back
  there, so `main()`'s endless loop needs no change. One pass is one millisecond exactly, so runs repeat to the
  millisecond. `hw.c` includes `controls.c` for its pin and key tables and feeds the ADC, GPIO and key RAM, so debounce,
  the 40 ms key scan, hysteresis and the mux scan are the real ones. It includes `din.c` too (from 2026-10-31), one UART
  interrupt a byte, so what the socket throws away (SysEx) doesn't reach the firmware here either. Stubbed: `sh.c`,
  `dac.c`, `tune.c`, `tuning.c`, USB, the EEPROM (a file, so a power cycle is a new process on it), the HT16K33 (its RAM is
  what tests read). `midistat`'s second number is DIN bytes waiting.
- **What it can't tell:** anything analog or timing on the hardware (a pass here is a flat 1 ms), USB (`usb_harness.c` covers
  the driver's register writes only: the review's section below), the bootloader.
- **What the 628 tests cover** (counted 2026-11-01, with the synced LFO times, the init sound's routes, the editor's Velocity knobs, the panel's Velocity routes and the route hold below; a class each, and 170 of them need `node`): boot on formatted, stock-format and untuned memory; manual mode; typing a
  slot; VALUE through the patches; saving; init, undo and compare; a damaged slot; every knob's display value and the
  envelope table; pickup after a load, a layer, LFO or envelope switch and with pickup off; Shift's secondaries and
  buttons; every Settings page, value and sub-page; the LFO controls; keyboard tracking; layers, links and voices per
  layer; MOD ASSIGN's sources, destinations, depths, scrolling and Browse; step recording and its steps; the clock
  sources, tap, SYNC IN and the beat LED; Tune; controllers on the display; two things at once (a load, a controller or a
  knob while a menu, a save, MOD ASSIGN or a typed number has the display); the envelope page; the face buttons; MIDI
  (pedal, all notes off, the channel set, channel, 8CH and MPE modes, bend, DIN, and over USB only the version and
  update-mode SysEx);
  voice modes and unison; key-up, DAHDSR and cycling envelopes on the VCA; the sequencer playing, once, under the
  pedal, saved with the patch; everything the manual says a patch stores, round-tripped through a save, the init sound
  and a power cycle; names; the scale snapping pitch; that every text in the name tables has glyphs; and from the review
  below MIDI floods, settings at rest, factory import, the host protocol, the clock after a tune, ticks held up,
  single-wire routes, the USB driver, the crash record and handler, no crystal, the pages and the preset bundle. Firmware
  state the display can't show is read through the host SysEx (settings, parameters, routes, the engine probe, CVs).
- The manual's envelope table said 150 and 310 ms at half; the curve gives 154 and 311, so the manual says that now.
- **The editor's Range column, rings and "where" strings against the firmware** (`WebRanges`, 32 tests, needs `node`): the
  page's own `swingUnit()`, `routeSwing()`, `swingWhere()`, `modRange()` and `format()` are cut out of `index.html` and
  run in Node, and each destination gets a route from FIX (always 1) in the simulator, measured on the CVs in the
  destination's own unit: pitch A / B / both at five depths (the squared curve, cents under a semitone), cutoff, the
  filter and poly-mod envelope amounts (semitones, `hw.c`'s CV is 256 a semitone around note 64 so ±96 fits), the LFO
  amounts and LFO → pitch / filter / PW for both LFOs (a step LFO with one step on is a constant 1), the VCA and the
  volume counting down (FIX by its depth, velocity from full), levels, noise, resonance and poly-mod B in knob units,
  pulse width as a share of the knob's travel, the LFO rates from the probe's phase advance (octaves), the amp and
  filter attack, decay and glide times as a factor (linear curves, so the times measure exactly), sustain as a level,
  unison detune in cents. The page's number, its text (with its rounding) and, for the cutoff, the LFO rate and the
  attack, its "where" endpoint against the knob's display value or the measured time, all have to agree; the rings'
  spans in knob turns too. A static test checks `swingUnit()`'s constants and `DEST_SCALE` against `synth.c`'s
  `#define`s. Not measured: Morph and the macros. Found nothing wrong in the page; found that a single-wire route
  (volume, detune, levels) does nothing until the layer has played a note, which is how `last_voice` works. A rate
  measurement's window has to come from the simulator's clock (`now()`): a probe runs 5 passes of its own, which
  inflated a 40 ms window by an eighth before `lfo_rate()` read the clock. From 0.9.4 the synced case is measured too:
  LFO 1 synced at 1/4 runs 2 Hz with no clock (120 BPM), and FIX +50 into its rate gives the page's 1/16 within 1 %
  (`lfo_rate_long()`, ten 40 ms windows); the Range cell's main/under texts and `noteValue()`'s names are pinned. A slot's
  sustain scale is fixed in `DEST_SCALE` too: a sustain route's arc was a third of its reach.
- **The pages' own code in Node** (2026-10-31, skipped without `node`): `run_node()` runs a page's script against a fake
  clock and fake MIDI ports, and a line `REQ <hex>` from the script is a request for the simulated firmware, whose reply
  comes back on stdin, so a page can be run against the real `main.c`. `WebInstaller` (13), `WebReconnect` (21),
  `WebRestore` (3), `WebRestoreOnStock` (2), `WebSaveInASlot` (5), `WebMarkingTheMemory` (2), `WebSmallThings` (8), `WebVelocityKnobs` (38);
  `PresetBundle` (4, Python) checks `presets/lib/bundle.py`.
- **Bugs it found on its first runs, fixed:**
  - **The patch loaded at boot lost its knobs.** `synth_update()`'s first pass copies the panel into both layers until the
    knobs are primed, and at boot that ran after `load_patch()`: the patch's buttons and routes stayed, its knob values
    became wherever the knobs stood, with no pickup. Very likely the old P66 report ("sounding wrong after start-up until
    reloaded", filter snap gone), not yet confirmed on the synth. `synth_apply_patch()` now primes them first. #14 made every
    boot hit this, so it matters more than it did.
  - Boot's version showed for one pass, and an older layout's mark never at all: the load replaced them. They get 1.5 s now.
  - `ForM`, `rSEq`, `SEqA`-`SEqd` and `FIX` had a blank digit: the font had no M, q or X. Added a small q and X (as H, as
    `FIHd` does); `ForM` became `Forn`. A test now checks every string in `main.c`'s and `lfo.c`'s tables has glyphs.
  - Shift + Rec's `no` (manual mode, or an empty slot) stayed until something else wrote the display.
  - After boot, Shift + Perf "undid" the boot's own load into manual mode: the load armed the undo.
  - A first Shift press within 400 ms of boot latched: `shift_tap_at` started at 0.
  - A patch load (program change, the host) wrote the slot over an open menu, MOD ASSIGN or a number being typed, and it
    stayed; Preset wrote `PAnL` over an open menu. `load_patch()` and `manual_mode()` go through `idle_display()`, which
    also shows a number being typed.
  - A program change during a save replaced the sound about to be stored, and moved the slot. It's ignored until the save ends.
  - The host's save moved the current slot without showing it. A controller's value now stays 1.5 s, as a knob's does.

## A review before the release: boot path, crash record, USB flow control, settings at rest, the flash tool (0.9.4, 2026-10-31; built, not flashed)

A review of the firmware, the tools and the pages, and its fixes. **Nothing in this section has been tried on the synth**: it
was built and tested on a computer only, and "Not tried on the synth" below says what that leaves open. The firmware version
stays 0.9.4.

**What it found, in short**

- A crash left no trace and looked like a hang (`Default_Handler` fed the watchdog for ever), and a dead crystal hung the boot.
- USB MIDI lost anything over 64 events a stall, for example 64 sequence steps = 448 events unpaced, and a lost note-off is a
  stuck note.
- Every step of a knob sweep and every tap wrote the global block, and each write stalled the engine and the CV refresh for up
  to 5 ms.
- The tool flashed a cut-short file without a word, and failed a flash only when asked (`--require-ok`), and then only on the
  last reply. `update-mode`, which leaves the synth without a firmware, took no `--yes`.
- The host protocol's format reply had 0x80 inside the SysEx, and the pages' gate on stock memory never fired (Restore
  everything, above).

**The boot path** (`src/startup.c`). The bootloader jumps in with its clocks, interrupts and peripherals running, so the order
matters:

- `Reset_Handler` is naked and sets MSP and CONTROL before any code uses the stack: the bootloader's SP may be anything, and a
  set FPCA or SPSEL would change how the first exception stacks.
- `reset_body()`: VTOR, SysTick off, NVIC cleared, then **HSI before the peripheral reset**, because the reset mask holds PWR,
  which must not reset under the bootloader's fast clock.
- `sh_safe()` **straight after the GPIO reset**, which lets the S&H enables float, not after the bss loop. It reads
  `RCC_AHB1ENR` back: the first write after a clock enable can be lost (errata), and that write sets the enables high.
- FPU on, then `FPCCR` set explicitly (ASPEN and LSPEN, the reset values, which the bootloader may have changed), then `.data`,
  `.bss`, `main()`. All of it before `.data` is registers and constants.
- **No crystal:** the HSE wait feeds the watchdog, `clock_init()` returns 0 and the chip stays on HSI at 16 MHz. `usb_init()`
  would wait for ever for a core reset without 48 MHz, so `main()` leaves USB off. DIN and the panel work; a DAC frame takes
  504 us and the refresh runs roughly ten times slower (computed and estimated, not measured). **SysEx is USB only** (`din.c`
  throws it away), so with a dead crystal no message reaches the bootloader, the crash record or any probe: the way to the
  bootloader is buttons 0 and 7 at power-up, and the record can only be read once USB works. Whether the bootloader's own USB
  runs without the crystal isn't known.
- `wait_ready()` feeds the watchdog: a format is about 384 pages with no main loop in between.

**The crash record** (`src/fault.h`, `src/startup.c`, `.noinit` in `link.ld`)

- Every exception and every interrupt nothing asked for lands in `fault_entry`, naked, using only registers until SP is safe,
  since a stack overflow must not fault again. SP goes back to the top of RAM, except when it's within 256 bytes of it: the
  frame is up there then, and `fault_report()`'s own stack would overwrite pc and lr before they're read.
- `fault_store()` in `fault.h` holds the logic, so the simulator's `faultstore` runs the same code. The frame is read only if
  it's aligned and within RAM; otherwise pc and lr are 0. `.noinit` sits after `.bss`, so the startup code leaves it alone, and
  it's NOLOAD, so it adds nothing to the `.bin`.
- **The streak:** the first and second crash reset; the third without 10 s of uptime between them stays put feeding the
  watchdog, so a crash at boot stops and a power cycle holding 0 and 7 reaches the bootloader. After 10 s of running the streak
  clears and the rest of the record stays for the probe. A start with a streak shows `CrSh` for 1.5 s, where the version goes.
- **SysEx 49** (read-only, USB) replies `49 <present 0/1> <streak, clamped to 127> <exception 0-112> <pc> <lr> <exc_return>
  <cfsr> <hfsr> <mmfar> <bfar>`, each 32-bit value five bytes high first (v>>28, >>21, >>14, >>7, v, each & 0x7F), 39 data bytes
  all under 0x80. With no record, present is 0 and the rest 0. **SysEx 4A 43 52** has no reply: it stores waiting settings and
  crashes. Any other length or data after 4A does nothing.
- **What a real provoke should leave:** exception 3, hfsr 0x40000000 (FORCED), cfsr 0x00008200 (BFARVALID + PRECISERR), bfar
  0x30000000, pc inside `fault_provoke`. A cfsr of UNDEFINSTR (0x00010000) instead means the address answered and the `udf`
  caught it.
- **What it can't do:** the bootloader runs between the crash and the next start and may use that RAM, and an overflow can trash
  it; the check word rejects a bad record. That doesn't only lose the record, it loses the streak: each crash counts as the first,
  so a boot crash would restart for ever (no EEPROM wear, and buttons 0 and 7 still work). `fault-test` on the synth decides this;
  if it happens, the streak moves to an RTC backup register. A hang and a watchdog reset are neither recorded nor counted.
- To read the machine code: zig 0.17's objdump has no disassembler and the ELF has no symbols, so `zig cc -S` with the firmware's
  flags plus `-Wno-unused-command-line-argument`, and decode the vectors from the `.bin`.

**USB flow control** (`src/usb.c`). MIDI input now makes the host wait instead of dropping.

- On XFRC the OUT endpoint re-arms only with room for 16 events (one full 64-byte packet) in the 64-slot ring, which holds 63.
  Otherwise it stays disarmed ("parked"): the core NAKs and the host's driver queues. The `usb_midi_read()` or `usb_midi_flush()`
  that brings room back to 16 re-arms it, with the OTG interrupt masked. So outside discard mode a burst of any size arrives whole
  and in order.
- Parked is cleared by a bus reset. SET_CONFIGURATION decides afresh from the room left (a nearly full ring stays parked until the
  next read). Stale XFRC flags are cleared at both.
- `usb_midi_discard(on)` keeps the old policy for long stalls: the endpoint stays armed and a full ring drops. Both autotunes
  (panel and SysEx 0x75) use it, through `tune_dropping_midi()`, so a 30 s stall neither blocks the host nor replays stale notes,
  and `drop_stale_midi()` still flushes at the end. A format (about 2 s) just NAKs.
- **Clock ticks can wait now.** Realtime bytes and Song Position share the endpoint. A parked endpoint reopens only after 16 reads,
  and a tick also waits behind whatever the host queued ahead of it: about 400 ms behind 16 program changes at about 25 ms each, and
  the full 2 s of a format. They aren't lost, but their stamps are that late. A parked ring needs at least 48 non-realtime events
  waiting during a stall, so neither a lone patch load nor clock alone parks it.
- **Tick timing:** `lfo_clock_tick()` treats one interval outside half to double the tempo as a late or early tick. A new tempo
  needs two such intervals alike, so a real jump takes hold one tick later than before.
- `handle_midi()` takes at most 64 events a call (four calls a pass), because re-arming in the read means a host flood might never
  run dry. A SysEx cut by the bound carries on at the next call. It reads DIN before USB, so a USB flood can't starve the socket;
  at 31250 baud DIN can't use up the bound.
- Compiler barriers sit before the ring indexes are published in `usb.c` and `din.c`.
- CLEAR_FEATURE (endpoint halt) and SET_INTERFACE now reset the endpoint's data toggle (USB 2.0, 9.4.5): a host resets its own, and
  the core ACKs a packet with the wrong toggle and throws it away, up to 16 events with no error anywhere.
- **`tools/sim/usb_harness.c`** (`build/sim/usbharness <scenario>`, class `UsbDriver`): the real `usb.c` against a model of the OTG
  registers; scenarios `burst`, `park`, `flush`, `reset`, `discard`, `realtime`, `toggles`, `preempted`, `random`. It tests the
  driver's register writes against the model, and passing it is not evidence of the NAK or race behaviour on the chip. EPENA
  set-only, and a bus reset clearing only USBAEP, are worst-case assumptions from memory of RM0390, not checked against the manual.

**Settings at rest** (`src/store.c`). CC A / B, the MIDI channel, pickup, the clock source and tempo.

- The setters only mark the global block; reads show the new value at once. `store_poll(now)`, once a pass, writes it a second after
  the last change, so a sweep or a tap series costs one write. A power cut inside that second loses the change.
- `store_flush()` runs before update mode, an autotune, a crash on purpose and the host's raw EEPROM reads and writes (0x5A, 0x79).
  A patch load, a save on unformatted memory and a format carry the settings anyway, as any write of the block does. If the settings
  change while the host is writing an image, the block lands on top after a second, as an immediate write would have done before.
- Writes retry at most 3 times until the next setter call, each failed try costing tens of ms by arithmetic (about 34 ms, not
  measured).

**Smaller fixes**

- The format reply is `5B 01 00 <failed>`: it was 00 80, with 0x80 inside the SysEx. Readers compute `b[0] << 7 | b[1]`, which reads
  the old form as 128 too. 0x79 past the end of the chip gets no reply, as 0x5A does.
- Sequence BPM from the host clamps to 255 (the field is a byte); under 30 still becomes 120. Route depth and macro offsets from a
  stored patch clamp to +-99 at load. A forced factory import clears the slot's sequence.
- The internal clock re-primes after every autotune and after direct mode. Before, a tune of 13 to 25 s left no ticks until the cycle
  counter wrapped (about 25.6 s at 168 MHz). The beat light kept free-running meanwhile and jumped back when the ticks returned, so the symptom is a
  phase jump (about 60 ms at 120 BPM after the simulator's 25 s tune), not a dark light.
- Single-wire routes use a layer's last voice only while that voice is still on that layer. After a split change, that layer's
  single-wire routes contribute nothing until it plays a new note.

**The tools** (both, unless it says Swift)

- `update-mode` is now `update-mode --yes`: without it nothing is sent and it explains. Notes above that say plain `update-mode` are
  history.
- `flash <file.syx> --yes`: the checks are the default. After the erase wait it waits for `03 31` for up to 7 s, stops at the first
  `01 00 xx` with xx nonzero, and needs `01 00 00` after the last block (up to 6 s; an earlier OK doesn't count). Exit is nonzero on
  any failure. Before `03 31` the failure text says "If the synth still plays, it wasn't in its bootloader and nothing changed. If it is
  there, it stays there until it gets a firmware...", since only that reply shows it's in the bootloader; after it, "The synth stays in
  its bootloader until...". `--require-ok` is accepted and does nothing. The Swift tool refuses a negative `--erase-wait` too.
- `flash`, `flash --dry-run` and `install` refuse a header-only file, a file under 10000 bytes, a header without `BEHRINGER PRO 800`
  at bytes 24-40 and a version at 42-44, a block count that differs from the header's size, and blocks out of order or missing. The installer page's `parseUpdate()` makes the same checks (run over stock 1.4.6, 0.9.3 and 0.9.4: all accepted).
- **New about the update format:** header bytes 10-12 (7 bits each, low first) hold the firmware size, always a multiple of 32. Each
  block carries 210 bytes and counts itself in bytes 10-11 (7 bits, low first). Blocks = ceil(size / 210). True of stock 1.4.6
  (139168 bytes, 663 blocks), 0.9.3 (78624, 375) and 0.9.4 (79232, 378). `install.html` doesn't check this yet.
- `install` needs no other flag and now has the 8 s port wait (above). New failure text when the port doesn't appear: the synth is in
  its bootloader, its MIDI port didn't appear, nothing was flashed, restart the MIDI service or replug, then the flash command.
- New `fault` (read-only, 0.9.4+) prints `no fault recorded`, or the exception by name, pc, lr, EXC_RETURN, CFSR and HFSR with named
  bits, MMFAR and BFAR (`(not valid)` unless CFSR says valid), streak. New `fault-test --yes` crashes the synth, waits up to 60 s for
  the restart and prints the record. It fails if there's no record, or if the record matches the one from before the crash with the
  streak cleared (it didn't crash, or took over 10 s to answer: switch off and on and run it again). It refuses at streak 2 or more.
- `format --yes`: a failed format exits 1. `eeprom-write`: a failed block after the first, or a mismatch, says the memory is half
  old, half new; success adds that the image carries the tuning tables. The Swift tool's mismatch now exits 1. `patch rename` says how
  many characters it dropped. `patches` lists a slot kind over 3 as corrupt.
- `patch load|save|clear|init`, `eeprom-write` and all parsers: the stock firmware's `01 00 01` gives "the stock firmware does not do
  this", too-short replies "short reply (n bytes)"; the Python tool turns any other IndexError into one line.
- Swift only: `check` refuses notes outside 0-127; `cc` and `bend` refuse `--channel` 0 or 17; usage says slots 0-127 or P1-P128.
  `check`, `dac-test` and `dac-heal` put direct mode back on failure through `onFail`, because `exit()` runs no defer.
- `presets/lib/bundle.py` fails the build on a slot outside 1-128 or taken twice, and on a name that isn't plain ASCII, is over 16
  characters or has spaces at its edges.

**How it was checked** (no synth): `python tools/test_panel.py`, 422 tests; `python tools/test_pro800.py`, 126 tests (468 and 133 with the synced LFO times, a section below), which replaces
`run_self` at import so no test can start the tool on the real port; `scripts/build.sh --no-pack`.

**Tried on the synth, 2026-11-01** (Windows, MIDI Services; `python tools/pro800.py`, this build)

- **Install and boot:** `install` took 55 s including the 8 s port wait, the bootloader gave `01 00 00` after message 388 of 388, and
  the new boot order runs. The whole EEPROM read the same before and after, and P69's settings, parameters and routes too.
- **The crash record survives the bootloader:** `fault-test --yes` came back in 3.6 s with HardFault, CFSR `PRECISERR BFARVALID`, HFSR
  `FORCED`, BFAR `0x30000000`, pc `0x08019A40`, streak 1. After 10 s `fault` showed the same record at streak 0. So the streak rule's
  storage holds. Three crashes in a row and `CrSh` on the display weren't tried: only one crash, and nobody was watching the panel.
- **USB flow control on Windows:** patch loads take about 14 ms each here, so an unpaced burst parks the ring. Separate sends of the
  load request, 50, 200 and 500 of them: every acknowledgement came back, the slowest send was 17 ms and the whole run took as long
  as the loads, so the host really waits. No send failed.
- **One buffer held over about 1 s fails on Windows, though it arrives:** one `midiOutLongMsg` of 80 loads or more returned error 1
  at 1.01 s, and every load in it was still done and acknowledged. 60 loads (0.73 s) passed. Neither tool nor page sends a buffer
  that big (one message a send), so this needs a host program with a huge buffer, or a send that a format holds for its 2 s. It's
  Windows MIDI Services' limit or the WinMM layer's; which wasn't looked into.
- **Notes:** 600 note pairs with 24 patch loads among them, unpaced: no held keys and every voice idle afterwards.
- **Autotune with traffic:** 25.4 s, 24 of 24 rows, 20316 messages sent during it with no send failing and no send pair over 1 ms; the
  next request answered in 1 ms and `engine` showed nothing held. So discard mode keeps the host moving. All of this was read from
  the firmware's state and the tuning comparator, not by ear: the line signal wasn't being monitored on the Move, so this build hasn't been heard.
- **Settings at rest:** `set cc-a` reached the block; the block read back right after 2 s. (A stored 0 means the default, and `set`
  takes 1-127, so putting CC 11 back leaves an explicit 11 in the block where a 0 was. It behaves the same.)
- **Oscillators after a tune:** within about 2.4 cents from note 36 to 96. Note 36 (65 Hz) often reads an octave high (about 3 of 16
  oscillators each run, even after `check`'s three tries, once +897, and a different few each time), which is `check`'s known
  comparator effect at low pitch reaching further up than its comment says. The same oscillators read right on the next run. Whether
  it got more frequent since 0.8.4 wasn't tested: nothing in the measuring code changed (src/tune.c untouched) but the old build wasn't run for comparison.

- **The filters track far less well through a warm-up than the oscillators:** a tune during the morning's session (how long the synth had been on isn't known),
  then the mean error at note 48 through those tables, every 5 minutes: +56, +89, +114, +134, +149, +162, +172, +179, +186, +191 cents
  at 47 minutes, each step about 0.79 of the last, so about 25 cents still to come (a time constant near 21 minutes, estimated). The
  error shrinks towards note 120, which hardly moves. The older tables (unchanged since at least 2026-10-30) read -247 cents at note 48 before that tune,
  which would be the same effect the other way round if they were made warm. A tune at 50 minutes read within 4.3 cents on every filter. The oscillators
  stayed within about 2 cents all along. It's the whole-scale error an uncompensated exponential converter would have with
  temperature (our reading, not measured on the chips). Nothing in this build changes the CVs: `sh.c`'s tenths-to-cycles gives the
  same values as before. So tune once it has warmed up, about an hour for the filters.

**Not tried on the synth**

- A host that sends CLEAR_FEATURE or SET_INTERFACE on a live connection (the toggle reset), macOS while parked, and a host sending
  during the 2 s of a format.
- **The Swift tool has not been compiled since these changes** (no swiftc on the Windows machine): run `scripts/tools.sh` on the Mac.
- The no-crystal start, the third crash in a row, and the reconnect, the restore and the installer's tabs in a real browser against
  the synth.

## Backlog

- `pro800 batch` (commands from stdin on one MIDI connection) for the preset installer.
- From the GitHub issues: a CC or NRPN for every parameter, with NRPN on the display (#12, #1; the reporter asks for
  it once the firmware has settled), release velocity as a mod source (#8: `key_off()` drops it), the
  Settings and Performance menus on a long press (#2, with Perf's init below), a one-press save (#1).
- Audition the preset bank on the synth and tune levels and cutoffs.
- Compare stock and PRO-8000 tuning. Stock's Tune takes clearly longer than ours (about 25 s) and, by ear on 2026-10-30,
  goes voice by voice through notes in different octaves, then PW and filters. Ours measures each oscillator and filter
  at fixed CVs and doesn't touch PW. Find out what stock's extra time buys and whether ours should do any of it.

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
- Make Perf's init a hold or double press.
- Scroll the patch name across the display on load; show the pickup direction (top / bottom segment); a velocity
  and aftertouch amount page in Settings; Tune progress on the display; hold a Settings key to reset it.
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

1. Skeleton: arm-none-eabi-gcc, linker script at `0x08010000`, `SCB->VTOR`, clock tree 8 MHz → 168 MHz (the chip's
   limit is 180; the firmware runs 168, which also gives USB its 48 MHz),
   build script → `PRO800_update_gen` → `.syx`. First test: light a panel LED or the display.
2. Before flashing anything: back up the patches (SynthTribe / SysEx) and keep the stock firmware `.syx` for
   recovery.
3. Drivers: DAC + S&H refresh, pot mux + ADC, panel switches, HT16K33, EEPROM, MIDI UART, USB MIDI.
4. Autotune with TIM3 input capture, then measure the cutoff, VCA and resonance curves.
5. Port the p600fw synth core (voices 6 → 8, add noise CV), then add the bitimbral mode and LFO sync.
6. Build the per-voice modulation path as a general mod matrix, then add MPE and the 8-channel mono mode
   on top of it.

## A synced LFO's delay and fade in note values (0.9.4, 2026-11-01; built and simulated, then tried on the synth: see the end of these notes)

Asked for: with an LFO in sync, its delay and fade in should be note times, like 1/32 or 2 bars.

- **No new setting, no format change, no new SysEx.** `lfo_time()` (`lfo.c`) reads the stored 0-99 by the LFO's own sync: free as
  always, synced `lfo_note_seconds(lfo_note_step(value / 99))`, the scale the envelopes' note values use (0-3 none, then 1/32 .. 8 bars).
  Those helpers moved from `synth.c` to `lfo.c`, which sits below it, replacing `synth_env_note_*`; the envelopes call them too.
- **Read live**, so a tempo change takes effect at once. `lfo_fade_gain()` always was. The one shot's wait was a countdown set at the key,
  so it became `waited` counting up against the delay, read live when synced; free it still compares with the time at the key. That time is always
  the free seconds, whatever the sync at the key, so a shot turned free during its wait goes as one free from the key, with the gain.
- **A synced gain never falls during a note.** The tempo is an average that every tick moves, and the end of a delay moves with it: at ticks 20-22 ms apart, 4 or 8 bars
  opened and shut up to seven times over a few hundred ms (measured on the simulator, before the fix). So `lfo_fade_gain()` takes the gain of the pass before (`was`, 0 at a new note) and,
  synced, stays at or above it. A faster tempo or a smaller value still opens it at once, and a slower tempo still moves an end that hasn't come. A slower tempo no longer
  shuts what has opened, which also keeps it with the one shot, whose wait was already over by then. **The price:** a larger value during a held note counts from the next key, where free mode shuts it at once.
  Free mode keeps nothing from pass to pass, as before. Turned free mid-note, a synced note's gain goes by the free time, so it can shut until then; that's rare and ends by itself, and is left alone.
- **A voice's `lfo_age` stopped at 100 s**, which free mode's 10 s never reached. Synced, 8 bars of delay and 8 of fade take 128 s at 30 BPM
  and 384 s at the clock's slowest (10 BPM), so the gain would have stuck part way or never opened. It stops at 400 s now (`LFO_AGE_MAX`).
- **The panel:** `show_shift_value()` shows `lfo_note_name()` while the LFO that was turned is synced; free stays the number 0-99, as before. It
  goes by a new `shift_value_pot` and `shift_value_lfo`, since `shift_pot` is cleared by letting go of Shift and the value comes half a second after
  the name: without them a quick let-go showed the number. That also fixes `ALL` and `on` / `OFF` in the same half second. Switching the sync
  needs nothing else: the sync is looked at when the value is shown, and a value already up stays for its moment.
- **The editor** has no setting to go by, so `lfoNoteTimes()` checks the firmware's version (from 0.9.4), for seconds on older firmware. Both tools do
  the same in `settings`, asking the version only when an LFO is synced. **The version can't tell an earlier 0.9.4 build** from this one, since `VERSION`
  stayed 0.9.4: a synth still on an earlier 0.9.4 shows note values and acts in seconds, until it's flashed.
- **Existing patches change timing, by design; their stored values don't.** With a synced LFO and a delay or fade: P62 Trance Gate (fade 56: 0.46 s → 1/4T, 333 ms at
  120 BPM), P66 BsSQ/LD.Split 1 (fade 85: 2.3 s → 2 bars, 4 s at 120 BPM), P68 Key-up WhopWhop (on the synth, not in `presets/`, so not looked at here). The presets' descriptions don't name those times.
- **Checked** on the simulator's CVs (`LfoNoteTimes` in `tools/test_panel.py`), the page's code (`WebLfoTimes`) and the fake synth (`tools/test_pro800.py`). **At the time it had not run on the synth**, and the Swift tool
  hasn't been compiled (no `swiftc` here): its text is only compared with the Python's. Counts as of this section: test_panel 468, test_pro800 133.

## Velocity on the init sound (0.9.4, 2026-11-01; built and simulated, then tried on the synth: see the end of these notes)

Asked for: new patches start with velocity wired to the VCA and the cutoff, and the editor gets a knob for each. The firmware
and everything that builds on the init sound come first below, the editor's knobs after them.

- **Two ordinary routes on both layers**, in the init sound's route table (`init_patch()`, depths in `INIT_VELOCITY_TO_VCA` = 70, 40 until it was tried on the synth
  and `INIT_VELOCITY_TO_CUTOFF` = 20): slots 0 and 1 of 16. No new field, no format change, no new SysEx, so nothing here needs a
  version check.
- **What they do, measured on the simulator's CVs** (`InitRoutes`): the VCA's gain is 1 - (1 - v) x depth, so 1.00 at velocity 127
  and 0.65 at 64 (0.85 at `play`'s default 100). The filter's CV is value x depth/99 x 96 semitones above the knob: +19.4 at 127,
  +9.8 at 64. Init's cutoff knob is at its maximum, but that doesn't make the route inaudible until it comes down, as the first
  version of this section said: see "The reviewers' findings" below.
- **Who gets them:** Perf, an empty slot however it's loaded (keys, VALUE, program change, 5C), formatted memory's first boot on
  P1, the 5F SysEx (`pro800 patch init`, the Patches tab's button). Shift + Perf still brings back the sound before.
- **Who doesn't:** stock-format and never-formatted memory boot on the panel with no patch loaded and no routes, until Perf or an
  empty slot's load. Manual mode needs a closer look than "no routes": `synth_manual()` only makes the knobs and switches live and
  has never touched the routes, so from the init sound the panel keeps those two (from any patch, that patch's). Left so: the
  mod matrix isn't on the panel, and clearing it would change every saved sound's manual mode.
- **The leak, and its fix.** Everything that builds a sound from JSON starts from the init sound and relied on it having no
  routes: `planSound()` (`sound-plan.js`: the editor's Load JSON, its Install bank, the installer page) and `plan.py`
  (`presets/install.sh`). Left alone, every preset would have gained the two routes on both layers, a sixteen-route preset would
  have lost a route, and a `"both"` route's bit (by slot, in list order) would have landed on the wrong route. Both now take the two
  off right after 5F, on both layers (`INIT_ROUTES`, one list in each, compared with the firmware by a test). Depth 0 on a route
  that isn't there has been acknowledged since `synth_set_route()` first existed, so a firmware without these routes takes the
  extra messages as it is. A new pair in `init_patch()` means a new entry in both lists; a new depth doesn't.
- **Looked at and left:** `.p8p` import, a backup's restore and the EEPROM commands write records, and a factory import converts a stock record: none starts from the init sound
  (`init_patch()` has three callers: Perf, an empty slot, 5F), and a factory sound stays without routes (tested). `gen_presets.py`
  takes only the init sound's knob values and lists each preset's own routes. The tools' text: `patch init` is "the basic sound
  (osc A saw), as PERF does", still true; `routes` printing "no routes" reads the synth. Neither tool's code changed, and the
  test table in `test_pro800.py` gained `route ... 0`, the line `plan.py` now sends.
- **Tests:** `InitRoutes`, `NoInitRoutesOnStockMemory`, `NoInitRoutesOnBlankMemory`, `PresetRoutes` (the 66 presets through `planSound()`,
  the editor's own `loadSound()` and `installBank()`, and `plan.py`'s lines, each on the firmware, compared with what the file lists), one
  each in `FactoryImport` and `WebInstaller`. Each of the 29 was seen to fail with one of 16 temporary reverts, every file then
  restored and its hash compared. Existing tests about something else start from `Synth.no_routes()`: `ModAssign`'s
  source/destination/depth, recent knob, depth zero and no-routes hold; `Browse`'s `setUp`; `ModAssignMore`'s negative depth, edit
  layer and routes-saved; `PatchStores.make`; `HostProtocol`'s out-of-range record; `WebRanges`' `setUp` (its
  `test_velocity_into_the_vca` still passed without it, but only because its first loop happened to remove the route). Two expected
  values truly changed, as the init sound replacing a saved sound now has its routes: `test_routes_are_saved_with_the_patch` and
  `test_everything_comes_back` expect `INIT_ROUTES` where they expected `[]`.
- **When this was written it hadn't run on the synth** (see the end of these notes for what has since). The timings measured earlier "on the init sound" (above) were without these two routes per voice;
  they're slow routes, evaluated once a pass, and haven't been measured. Neither have the knobs: nothing here has touched a port.
- **The editor's two knobs** (`velocityKnob()` in `index.html`). A Velocity knob in the faceplate's filter section (Velocity → Cutoff,
  -99 to +99) and amp section (Velocity → VCA, 0 to 99). Each is the depth of the edit layer's route: it reads `layerRoutes`, writes with
  `setRoute()` (56), and holds nothing but the position of a turn the synth hasn't confirmed. The matrix, the rings and the knob read the
  same list, so they agree whichever changed it, MOD ASSIGN included (the page's poll brings that in). No new firmware, SysEx or version
  check: Velocity is source 4, the cutoff 3 and the VCA 9 in the first firmware with routes (29e8b88), and 56 and 57 are as they were; a
  test runs the knob on a short 57 reply with 8 slots, as that firmware's table was.
  - **Where:** first in each section's envelope row, so Velocity sits under Cutoff and straight above the amp's. To keep those rows the
    same, the filter's caption ("Filter envelope", "Env 3" ..) now runs under its four ADSR knobs rather than under the display too.
    Measured in a browser on a scratch copy with `requestMIDIAccess` stubbed out, at widths 1720, 1600, 1440, 1281, 1280, 800, 641, 640 and
    375: the two Velocity knobs and the two ADSR rows share their left edges and tops at every width, and the page doesn't scroll sideways.
    What it costs: a section under about 525 px can't hold five knobs and the envelope display side by side, so the display goes under the
    knobs. That's the page's widths from 1281 to about 1450, 641 to about 710, and about 570 and under (worked out from the measured
    widths): 163 px tall instead of 124 for the amp. At 375 the Velocity knob takes a line of its own over the ADSR row. The rest of the faceplate's layout, breakpoints included, is unchanged.
  - **A turn:** debounced 60 ms like `settingKnob()`, so a drag sends once it rests. Unlike `settingKnob()` its hold lets go when the
    write and the re-read after it are back, so a change anywhere right after a turn shows at once; the 1.5 s stays as the limit when
    nothing answers. A later turn during a write keeps the hold (`turn`), a poll's answer doesn't move a knob that's being turned, and the
    hold is per layer. The write goes to the layer the turn was made on, even if the layer changes inside the 60 ms.
  - **On a change of layer** `refreshParams()` moves `paramsLayer` first and reads that layer's routes several requests later. New
    `routesLayer` says whose `layerRoutes` is: until they match, the knob keeps what it showed and ignores a turn, as an empty list couldn't say
    whose it was.
  - **A full table:** with no route to turn and 16 in use, the status line says so at once (nothing's sent, since the firmware just
    wouldn't answer for 1.5 s) and the knob doesn't move. If the panel filled the last slot behind the page's back, the unanswered 56 does the same, after
    the re-read; no answer with a slot free says "The synth didn't take that route".
  - **A negative Velocity → VCA route** (the matrix can make one). On its own it does nothing, but it isn't quite nothing: measured, beside
    the wheel into the VCA it takes back part of that route's drop (`VelocityOnTheEngine`). So the knob shows it as it is: resting at 0, the
    number in red in place of the label even without Values, the reason in its title. A click, a drag or a key downward writes nothing. Turning up replaces
    it, and a double-click removes it, both being the person's own act.
  - **A double-click goes to 0**, not to the init sound's depth as other knobs' defaults do: a third copy of those depths would undo the
    two named constants in `main.c`, and off is what the double-click on the other model knobs does.
  - **Both:** the knob edits the edited layer's own route only. If that route is marked Both it reads `+40 both`, its depth moves for both
    layers' voices, and at 0 the firmware drops the mark with the route: the status line says so and it comes back as this layer's alone.
    The other layer's Both route on the same destination reads `+L1` / `+L2` beside this layer's depth and in the title, and isn't touched,
    though the matrix's row for it can be. Neither shows outside the two-layer modes, where the bit does nothing. A first try showed a removed route as still
    marked until the next poll, as `routeBothMask()` reads `rawSettings`: a Both route's removal now re-reads the settings first.
  - **Links** don't reach routes: the engine reads a voice's routes by its own layer (`VelocityOnTheEngine` measures layer 2's route
    under layer 1's cutoff knob), so a linked layer 2 keeps its own Velocity routes, the knob isn't marked `linked` and its title says so.
  - **The matrix's depth slider** now re-reads the routes after its write, so the knob and the rings follow at once rather than at the next
    poll; `renderRoutes()` still doesn't rebuild the row while the slider's in use, which no longer means "has the focus" (below).
- **Tests:** `WebVelocityKnobs` (38): the page's own knob, `adjustable()`, `dial()`, `refreshLayerRoutes()`, `renderRoutes()`, `modRange()`,
  settings and layer changes on a small fake DOM (`VELOCITY_DOM`), against the simulated firmware, with the two knobs built from the very
  expressions in `buildPanel()`; `VelocityOnTheEngine` (3). Each of the 41 was seen to fail with one of 56 temporary reverts, and every file
  was restored and its hash compared. The layout itself isn't in a test beyond "first in the envelope row"; it was looked at, as above.
  `InitRoutes.test_layer_2_plays_them_too`, from the step before, played note 40, which on a split is layer 1: it now plays 72, and
  also checks that layer 1's routes aren't what it's hearing. Counts as of this bullet: test_panel 538, test_pro800 133.
- **The reviewers' findings on all this**, each checked first on the simulator with the page's own code (the tests below, run on the page before the
  fixes), the same day, also not on the synth:
  - **Velocity → cutoff isn't silent with the knob at the top.** The brief, the first version of this section and MANUAL said it was. `cutoff_rest` is
    the knob plus keytrack × (pitch - 60), the knob's top is note 132 and init's keytrack is 1.0, so the CV moves the route's whole 19.2 semitones between velocity
    1 and 127 at every note (measured at 24, 36, 60 and 96, no end stop). In note space, worked out, not heard: note 36 was 4.2 kHz at any velocity and is now
    4.2 to 13 kHz, note 24 is 2.1 to 6.4 kHz, and note 60 and up start at 16.7 kHz. So **the new init's low notes are brighter than the old init's** with the knob untouched.
    The decision stands as it was asked for (depth 20, knob at the top); the owner should hear it. MANUAL and the knob's title say so now.
    A measurement on the synth that shouldn't depend on velocity has to take both routes off, on each layer it plays: `route 1 velocity vca 0` and `route 1 velocity
    cutoff 0`. **CLAUDE.md's rule still names only the first, and `play --velocity 127` as the way to compare with older measurements**, which holds for the level but not for
    a low note's timbre: CLAUDE.md was left for the owner to change.
  - **Both on init's own route doubles it on the other layer**, as a Both route adds to that layer's own copy: measured on a split at velocity 64, layer 2 goes from 0.80 to 0.60 and layer 1 stays.
    That's how Both has always worked; what's new is that init gives it a twin to add to. Left in the engine, said in MANUAL, the cheat sheet and `sound-design.html`.
  - **With layers off, taking layer 1's routes off leaves layer 2's** in the saved patch, where they play once it has two layers. It follows from "per layer"; left so, and
    said in MANUAL. Nothing on the panel or the page points it out at that moment.
  - **An older page or `plan.py` on this firmware still leaks**: `planSound()` as committed sends 5F and removes nothing, so every preset would get two routes per layer, a list-order `both` bit lands on
    the wrong slot, and a sixteen-route preset stops with a message the synth didn't take. No fix is possible from here: a firmware can't tell which page is talking, and the routes are the decision. So **the page and
    this firmware have to be published together**, which `scripts/pages.sh` does as one site; until that push (none for now), the published editor doesn't go with a 0.9.4 built here. It's in MANUAL. No test: the old page isn't in the tree.
  - **The matrix followed the focus, not the use.** `renderRoutes()` skipped a rebuild while anything in the table had the focus, and a slider keeps it after the mouse lets go, so after
    a drag MOD ASSIGN or a panel layer change left the knob and the matrix disagreeing until something else was clicked. In use now means: a slider held (window `pointerup`, `pointercancel`, `blur`, or the slider's
    own `change`), 1.5 s after a slider's move (write and re-read on their way), and 10 s after pressing a source or destination menu, as a rebuild would shut it and nothing says when one closes unpicked. Outside those a rebuild happens, and puts the
    focus back on the same control of the same route. A menu opened from the keyboard gets no hold: a route changed behind its back shuts it.
  - **A slider edit back to the depth last built left the row wrong for good**, as `routesShown` was only set on a rebuild, so the re-read matched it. A slider's move now clears `routesShown`, so the next rebuild always happens. One rebuild more after every slider drag, once its hold ends.
  - **A negative 0-to-1 route on the VCA or the volume read "0 to +40 %".** Measured, it adds nothing alone. `routeSwing()` now says "none on its own" for it (the knob's screen, the Range cell) and `modRange()` draws no ring on the volume knob, whose
    title still lists it. The volume's clamp is the same code as the VCA's (`clamp01(1 + mod)`), read rather than measured.
  - **The knob's status line outlived what it said.** A later turn that works now takes away the knob's own message, back to `showFirmware()`'s line; anyone else's stays.
  - **A route stored twice** can only come from a hand-made record (every write overwrites the first match, JSON and the matrix's menus included). The knob reads the first with `×2`, says so in its title, turns the first, and 0 now takes them all off.
  - **A drag across a layer change** wrote the first layer's starting depth to the second at the next pixel. `adjustable()` now remembers the layer a drag began on and ignores the pointer after a change, for every knob, since `knob()` and `settingKnob()` had the same gap; only the Velocity knobs have a test for it.
  - **Tests:** three in `InitRoutes`, two in `VelocityOnTheEngine`, seventeen in `WebVelocityKnobs`, each seen to fail with its fix reverted and every file restored and its hash compared. The driver's `touch` now keeps what it was told, and `showFirmware` can be replaced. Nothing here has touched a port: it's the simulator and the page's code on a fake DOM. The matrix's part was also run once in a real browser (the desktop app's), on a scratch copy of the page with `requestMIDIAccess` stubbed out and `layerRoutes` set by hand: the hold through `pointerdown` and `pointerup`, the rebuild with the focus put back, a menu's hold and the "none on its own" cell behave there as on the fake DOM, with nothing on the console. That copy is offline, so its knobs didn't paint and weren't looked at, and a real mouse drag wasn't tried. Counts as of this bullet: test_panel 560, test_pro800 133.

## The Velocity routes on the panel, and pickup's gesture moved (0.9.4, 2026-11-01; built and simulated, then tried on the synth: see the end of these notes)

Asked for: the two routes the init sound starts with get a panel gesture, the same ordinary routes as MOD ASSIGN and the editor's Velocity knobs. **None of this has run on the synth.**

- **The gestures.** Shift + M VOL is the edit layer's velocity → VCA (`VE.A`, 0 to 99), Shift + FIL ENV AMT its velocity → cutoff (`VE.F`, -99 to 99), through `shift_turn()` like the other Shift knobs: name, then value, by how far the knob turns. `V` is the display's small u, so the names read `uE.A` and `uE.F` on the digits. `EveryName` now reads `shift_turn()`'s table too, which it didn't, and names these.
- **Knob pickup moved to Shift + PMOD FIL ENV** (`P.UP`, a quarter turn, as before). **It was Shift + FIL ENV AMT in 0.9.3**; the setting is stored where it was (setting id, global block), only the gesture moved. Shift + PMOD FIL ENV did nothing before and doesn't touch the poly-mod value.
- **M VOL had to change, which wasn't asked for.** The amplifier read `panel[POT_MASTER_VOL]` straight from the knob, Shift or not, so the route's gesture would have moved the level with it. The level now comes from `layer_param[0][POT_MASTER_VOL]`, which follows the panel as long as the knob is latched, and `synth_set_shift()` no longer leaves M VOL out: under Shift the level stays. After a turn under Shift, M VOL waits like any knob (pickup, the dot; with pickup off, it jumps when moved), since otherwise letting go of Shift, or the knob's next move, would change the volume at once. A knob not turned under Shift is back within the pickup window in the first pass, so without that gesture M VOL behaves as ever. A load leaves a waiting M VOL waiting (it was forced to latched); MOD ASSIGN doesn't freeze it. **The cost:** with the knob left at the bottom after an edit, it has to come up past the level before it can turn it down. That's the owner's call to look at on the synth.
- **The editor's knobs' awkward cases, the same here.** A full table with no route to adjust: `FULL`, nothing changes, at any turn of the knob (the editor only says so on a turn up, as its knob can't go down from 0), and no value follows it. A negative velocity → VCA route shows as it is, a turn down writes nothing and winds nothing up, the first whole step up replaces it from 0. 0 takes a hand-made record's other copies off too.
- **The fraction.** `shift_acc` is per knob. For the two routes and `P.UP` what goes past an end is dropped, so turning up from 0 adds the route after the same turn each time, and `P.UP`'s quarter turn is a quarter turn from either end. It wasn't on 0.9.3's knob: up 0.2 of a sweep while on, and it took 0.45 down to go off. `P.UP`'s is also cleared on a Shift edge (`shift_set()`), since its step is a quarter turn and 0.24 turned in one press plus a touch in the next turned it off. The routes' isn't cleared on a change of layer or of Shift: under a step. The other knobs stay as they were.
- **A knob at the top of its travel (the owner's call; the hold in the next section takes the mark's half of this away, the clamp stands).** The knobs are relative and only clamping at an end changes the offset between knob and depth. From the top, `VE.A` at 40 goes down and back to 40 at most; to get higher it has to pass 0, which removes the route and its Both mark, with nothing on the display about the mark (measured: 40, 18 at 0.8, 40 at the top, gone at 0.5, 56 back at the top, Both 0). `VE.F` the same, for the same reason, though a sweep of it re-adds the route on the far side of 0. On the other Shift knobs clamping loses nothing. The way round is in the manual and tested: with pickup on, a knob turned under Shift waits, so it can come down without Shift as far as its own value. With pickup off it would move the value, and that way round isn't there. If the owner wants otherwise: keep the mark over a 0 within one hold of Shift, or leave the route at depth 0 until Shift is let go. Neither was tried.
- **A slow turn of `VE.F` stops on 0.** The knob's smallest reported move (24 of 4095, `POT_HYSTERESIS`) is 1.33 steps of `VE.F`, so about every fourth move is 2 steps and it went from +1 to -1 past 0, which is the only way to remove the route. Crossing 0 by up to 3 steps now lands on 0 and drops the fraction; a faster sweep goes on through, so one sweep still reaches both ends. Found by the test.
- **Nothing to mark.** There's no edited flag: a save captures the routes as they are (`synth_capture_patch()`), and a save waiting for its second Rec takes the turn with it (tested). The Both mark stays while the depth is adjusted and goes with the route, as `synth_set_route()` does for everyone. A link between the layers doesn't reach a route, so on a linked layer the gesture edits that layer's own, and no link LED comes on as it returns before that code.
- **Left as they are.** Under Shift + Rec (compare) a turn edits the compared copy and is thrown away with it, as `nAC1` is. Shift drops a number being typed in a save, as it did, so a save to a typed slot goes back to the current one. The editor's Sound tab reads the routes on its 900 ms poll (`refreshParams()`), so its knobs should follow a panel edit as they do MOD ASSIGN's; read in the code, no test turns the panel with the page open. The sign shows as MOD ASSIGN's does: `-40`, and no plus.
- **Tests:** `ShiftVelocityRoutes` (27) drives it all through the panel; `Shift` and `Pickup` have their `P.UP` cases on the new knob and no fewer. Ten of the fixes above were each reverted, seen to make a named test fail and restored with their hash compared: the full table, the negative route, the fraction, the stop on 0, the copies, `P.UP`'s knob, the level's source, Shift taking M VOL, a load leaving it waiting, MOD ASSIGN not freezing it. The two tests of `P.UP`'s fraction were each seen to fail before its fix went in, at the point the review named; the third new one, the knob coming down without Shift, describes what the code already did and has had no revert. No port was opened. Counts as of this section: test_panel 587, test_pro800 133.

## A route at 0 waits 5 s (0.9.4, 2026-11-01; built and simulated, then tried on the synth: see the end of these notes)

Decided by the owner: depth 0 no longer removes a route at once, so sweeping through 0 (+20 to -20), or stopping to hear it, loses nothing. **None of this has run on the synth.**

- **The one place.** `synth_set_route()` is the only writer besides a patch load and a layer copy; MOD ASSIGN, the Shift knobs and the host's 56 all reach it. Depth 0 keeps the slot, source, destination and Both bit. `synth_route_poll()`, once a pass with `ms_ticks` as `store_poll()`, removes a route that has rested at 0 for `ROUTE_HOLD_MS` (5000, `synth.h`), and its Both bit. A depth brings it back inside that, in its slot, with its mark, and cancels it; 0 again at 0 doesn't start it over. A bit and a `uint32_t` per slot; the poll stamps the time on its first pass after the 0, so a route can stay longer by a pass, never less. 0 sets a hand-made record's copies all to 0 together, so the Shift code's loop went. The stop on 0 and the fraction stay.
- **It plays nothing, measured.** `evaluate_routes()` adds `value * depth`, which is 0 for every source and destination: the VCA and volume count-down `(value - 1) * 0`, FIX `-1 * 0`, the squared pitch curve, the envelope times' `m ? 2^x : 1` (a -0 is false there too), morph. Read, then `RouteHold` compares the voice's four CVs and every wire, exactly, with no route and with one at 0, for 8 sources into all 46 destinations, reaching 0 with no voice sounding, and a morph source on a real target. What a route at 0 still does: a source of Env 3 / 4 keeps those running (`uses_env34`), which nothing hears, and a fast route keeps the tick evaluating it.
- **A full table.** A new route takes the first empty slot, else the first at 0: `depth == 0` is the rule everywhere, in `synth_set_route()`, `velocity_route_full()`, and the editor's `routesTaken()` and Add route, since 57 shows the depth and no more. No new SysEx.
- **A save stores none at 0** (`synth_capture_patch()`): each stays in its slot number, so the Both bits hold, and its bit isn't stored. The one in RAM keeps waiting. The capture is also what undo and compare keep, so **a route waiting at 0 is gone after a compare or an undo**: it was on its way and nothing can bring it back then. `synth_apply_patch()` clears every wait, so a stale time can't take a route of the patch that came in; `synth_copy_layer()` copies none at 0 and clears the target's.
- **A record holding depth 0** (only a hand-made one; no firmware stored one): it loads, stays at 0 playing nothing with no time running, so nothing vanishes by itself. A new route may take its slot, a save leaves it out, and 0 sent to it starts the hold. The editor's Range text says 5 s for it too, which isn't so until then.
- **The presets' Both bits were the catch.** `planSound()` sends 0 for the init routes, then the list, and worked out each `both` bit from the list's position, as the table used to be empty by then. Now the two wait in slots 0 and 1, so a list starts at slot 2 and only the 15th and 16th take those two, unless the plan outlives their 5 s and the list starts at 0 after all. So no slot is worked out ahead of time: `planSound()` returns the marked routes as `both`, and after its messages `markBothRoutes()` (`sound-plan.js`, the editor's two paths and the installer's) reads 57 and sends the bits. A route that isn't at 0 doesn't move once it's in, so that holds however slow the plan was, and on a firmware without the hold (the installer with Flash unticked) with no version check. No reading stops the install with a message rather than guessing. `plan.py` reads the slot with `route-both` and needed no change. So the slots of a saved preset aren't its list's order any more; nothing reads them that way but `test_panel`'s old `assertLists`, which now compares routes with their marks as a set and checks no mark on an empty slot. It doesn't treat depth 0 as absent: unsaved it waits the 5 s, and from a saved slot there are none, which is the save rule's proof.
- **Decided along the way.** Both tools' `routes` prints a row at 0 marked `(plays nothing; 5 s at 0 removes it)`, and `no routes` only for an empty table. The matrix keeps the row and its Remove button leaves it at 0 for its 5 s too: the page can't remove at once with no new SysEx. The 900 ms poll drops it. A JSON read back leaves rows at 0 out, the lane and morph menus read past them, the knob reads `off` (with `both` if marked) and no `×2`. The editor's knob keeps its extra zeros for a copy: redundant on this firmware, needed by older ones. `routesFull()`'s text stays, as it's only said when none is at 0.
- **Browsing:** a row at 0 lists with depth 0. When one goes the list shortens at the next frame: `browse_show()` clamps, and none left reads `nonE`, already handled for no routes. The number shown then belongs to whichever route moved up. MOD ASSIGN left at 0 shows 0 throughout, and a turn adds it again, unmarked.
- **The known limit stands:** a Shift knob at the end of its travel can't go further. Going down and back up to get more room no longer costs the mark unless the depth rests at 0 for 5 s.
- **Tests:** `RouteHold` (26), 5 new cases in `WebVelocityKnobs` and 2 in `PresetRoutes`, two in `test_pro800`. Tests that assumed removal at once now wait it out and check the row at 0 first: `Synth.no_routes()` and the rest. Page tests get firmware time with a `WAIT` line in `run_node()`. Twenty pieces were each taken out, seen to make a named test fail and restored with their hash compared: removal at once, the poll, a removal's Both bit, 0 again not restarting, a depth cancelling, taking a waiting slot and its wait, an empty slot first, `velocity_route_full()`, the save's routes and its Both bits, the load's clear, the copy's two, all copies to 0, `routesTaken()`, Add route's count, the `×2`, the Range text, and the slot prediction `markBothRoutes()` has since replaced. Not reverted: the tools' printers, the JSON read-back and the lane and morph menus, whose page tests only check the code is there. No port was opened.
- **Found in review, each seen to fail on the code before its fix.** A plan slower than the hold put the marks on other routes, with no error: a 300 ms-a-message run left the list in slots 0-2 and the bit on slot 2. Reachable through a stall, as `ack()` allows 3 s a message, and only by a JSON or bank with a `both` route, which none of the 66 presets has (`test_the_marks_land_right_however_long_the_plan_takes`). The installer passed no `beforeHold`, so on older firmware the bit landed two slots off (`test_a_both_mark_goes_by_the_slot_the_firmware_gave_it`, on a fake table with no hold, the only older firmware here). And the page's Copy LFO relied on 0 clearing a mark: a route re-added inside the hold kept it, so layer 1's voices played their own 50 plus layer 2's (`test_copy_lfo_leaves_the_routes_it_replaced_unmarked`); it now clears the marks of the target's rows from that LFO first. The lane and morph menus send 0 then a depth too, but what comes back there is the same layer's own route, so it keeps its mark as any route back from 0 does. Counts as of this section: test_panel 623, test_pro800 135.

## Real-unit ranges in the mod matrix and per-source rings on the knobs (2026-10-31)

- The web page's Mod matrix got a Range column: `routeSwing()` turns a route into what it can add in the
  destination's own units, from the engine's constants (`synth.c`): pitch 24 semitones at 99 with the squared depth
  (cents below a semitone), cutoff 96, filter env amount 72, poly-mod env amount 48, LFO → pitch 12 and → filter 48
  semitones of LFO depth, PW ±50 %, detune ±50 cents, the LFO amounts, LFO → PW, VCA, volume and morph in per cent,
  LFO rates in octaves (4 at 99), stage times and glide as a factor (2^(4 × mod): ÷4 to ×4 at 50), the rest (levels,
  noise, resonance, poly-mod B, sustain, macros) in the knob's 0-99. A source that swings either side of the middle
  reads ±, a 0-to-1 source one way (the depth's sign), FIX a fixed offset; into the VCA and the volume a 0-to-1 source
  counts down from full and FIX down by its depth. `swingWhere()` adds where that takes the knob from where it stands
  for a cutoff (Hz), a free LFO rate (Hz) and a stage time or glide (seconds); the cells repaint with the knobs, and
  the new-route row previews its range before it's added. **0.9.4** (asked for: octaves aren't intuitive for a rate):
  an LFO rate's cell leads with the rates it reaches (`paintRange()`), the octaves under them. Free: Hz with no end stop,
  as the engine multiplies without one (the old string clamped to the knob's travel). Synced: note values from
  `noteValue()`, the knob's division ÷ 2^octaves named as the nearest straight, dotted or triplet value (`~` when more
  than 2 % off, bars from a bar up), where it showed nothing. A fixed offset reads as one value, for a cutoff and a
  time too.
- The knobs' rings: with two or more sources on one knob, `modRange()` groups the routes by source (`parts`) and
  `dial()` draws a thinner arc per source under the ticks (radii 23.5, 21.9, 20.3, 18.7, width 1.2, opacity .5; past
  four sources the fourth arc takes the rest), outer to inner in route order, and the combined arc moved from under
  the ticks (22.2, width 3.2) to just outside them (25.7, width 2), so it reads on top. The dial's title lists the
  routes in ring order with their ranges; the macro knobs do the same for their inputs. Checked offline in the desktop
  app's browser with injected routes (five sources on the cutoff, two on Osc A, FIX and velocity into the VCA, LFO 1
  into the amp attack, random into glide); not yet against the synth.

## Tried on the synth, 2026-11-01 (the build with the velocity routes, the Shift gestures and the hold at 0)

Windows, `python tools/pro800.py`. This covers only what's listed; the sections above that say "not run on the synth" still hold for the rest.

- **Install:** the bootloader gave `01 00 00` after message 394 of 394, and every EEPROM byte read the same before and after.
- **A flash loses what isn't saved:** P68 had three unsaved edits in RAM (amp-rel 72, env3-sus 0, env3 to pitch 89) and the restart reloaded the stored values. They were put back by hand from the `params` and `routes` read just before. Buttons and the sequence weren't compared. Read the sound before a flash, or warn that it goes.
- **The init sound's routes:** `patch init` listed velocity to VCA 40 and velocity to cutoff 20 on both layers, nothing saved. Played by hand with this build, the owner set the VCA's depth to 70 and kept the cutoff at 20: at the knob's maximum velocity is hardly heard in the filter, and it works once the knob comes down.
- **The hold at 0, timed on one MIDI connection against 0x57:** a route at 0 was removed after 5.03-5.04 s in 4 of 4 runs. Back to -20 at 4 s, it was still there 3 s later. A row at 0 reads back in `routes` with its note.
- **Synced LFO times:** with `lfo-sync clock`, `settings` prints `lfo-delay 60 (1/4)` and `lfo-fade 2 (off)`.
- **Not tried on the synth:** anything heard (the line signal isn't monitored on the Move), the Shift gestures and M VOL's new level path (need hands on the panel), the editor's Velocity knobs in Chrome or Edge, the delay and fade's actual timing, the Swift tool.
- **Tried by the owner, hands on the panel and ears on:** `VE.F` through 0 keeps the route; M VOL's new level path, the pickup gesture, the editor's Velocity knobs and the synced fades all work; the sound is fine. That is what set the VCA's depth to 70.
- **Found while changing that depth, and fixed:** with pickup off, letting go of Shift while a knob's reading was still gliding jumped the knob's parameter to the knob, because what was left of the glide read as a move. On M VOL that's a jump in the volume. Waiting now compares the readings, not the glide (`synth_tick`'s `pickup_from`), for every knob behind the muxes. `test_with_pickup_off_letting_go_during_the_knobs_glide_moves_nothing` fails without it. The build the owner tried has the bug, with pickup on it can't happen.

## CC 74 is cutoff after all (0.9.4, 2026-11-01)

0.9.4 had moved cutoff to CC 79, as 74 was taken for Timbre first and never reached the knob. The owner wants the usual numbers: cutoff 74, resonance 71. So 74 moves the knob and still feeds the `Timbre` source. In `8CH` and `nPE` it's Timbre alone: MPE keyboards send it for each note, and it mustn't sweep the other voices' cutoff (P57 and P58 route it). CC A or B set to 74 keeps it off the knob, as it does for any other number. CC 79 does nothing now; it was never released. A patch outside those modes with a route from Timbre now gets the knob moving as well: none in the bank. Simulated only so far.

## Unison voices, detune to 100, mono restarts Env 3 / 4 (0.9.5, 2026-11-02)

Issues 29, 30, 31. `stack_size()` is how many voices a unison note takes: the patch's 2-8 (`unison_voices`, record byte 355, 0 = 8), no more than the layer has. `stacked()` is unison in poly with room for two stacks or more; then `layer_note_on()` allocates by stack, `allocate()` stepping over each stack's first voice, and a note off needs no change as it already matches each voice's note. Otherwise unison is the one line it was. Mono and legato always play one line, on one stack. The spread in `voice_slow()` runs across a stack.

- **The setting filled the settings reply:** the new one is number 91, which moves the four read-only ones to 92-95 in the tools' tables and takes the 54 reply to 203 bytes, so `SYSEX_MAX` went up by 2. The page tells this firmware by the setting (`FEATURES.unisonVoices`), and against an older one hides the voices knob and stops the detune knob at 50, which that firmware would refuse above.
- **Detune:** only the range moved. A route to detune stays ±50 cents at depth 99, as rescaling it would change every patch with one.
- **Env 3 / 4 in mono:** reported as not retriggering on legato notes, and P71 showed why (Env 3 → Pitch A and B at full depth, sustain 99, mono + unison). Measured with its values in the simulator, adding D3 to a held F3 moved the cutoff 54 semitones and the pitch 0.00: every envelope re-attacked from its level, and Env 3's was already 1. The retrigger had always happened; there was nowhere to go. `gate_on()` now zeroes Env 3 / 4 in `VOICE_MONO`, outside the per-channel modes. Other envelopes: see the next section. Legato doesn't change.
- **Not tried on the synth yet.**

## A replayed note's attack, and the sustain's lag (0.9.5, 2026-11-02)

Issues 32 and 33, by the same reporter, who wondered whether they were one bug. They aren't: 32 is the cause of 31 again, and 33 is the shape of the decay.

- **Issue 32:** every retrigger attacked from the level, so with a long attack a note played 20 ms into its release took 415 ms to 90 % and not the 2530 ms of a fresh one (simulator, attack knob 85). A retrigger now fades out first, linearly, full scale in `RETRIGGER_FADE_S` (8 ms), then attacks from 0. It's in `ENV_DELAY` (`fading`), before any delay, so the replayed note takes the same 2530 ms. Below `RETRIGGER_FADE_ATTACK_S` (30 ms of attack) it still attacks from the level: from 0.8 that's over in a few ms, and a fade would be a gap and no gain, so plucks and leads keep what they had. It reaches any envelope that retriggers, also poly, a stolen voice and legato's first note. A voice that's silent has nothing to fade, so a first note isn't held up.
- **Issue 33:** there's no sustain stage. `ENV_DECAY` goes on heading for the sustain level at the decay's rate, so a knob move took as long as a decay (90 to 20: 0 ms at decay knob 10, 125 ms at 60, 1030 ms at 85). Past the decay's time (`timer >= decay_s`) the rate is now `SUSTAIN_FOLLOW_S`, 20 ms to -60 dB of the way, so under 20 ms for any decay. That also speeds up modulation routed to a sustain. Turning the decay up while holding a key gives the slow rate back until the timer passes the new time.
- **Not tried on the synth yet**, nor heard: whether the 8 ms fade is clean on the real VCAs is the thing to listen for. The test only says no step is over a quarter of the level in 1 ms.
