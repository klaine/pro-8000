# PRO-8000: user manual

For PRO-8000 0.9.2, an alternative firmware for the Behringer PRO-800, flashed through the synth's own bootloader.
It's an independent project, not affiliated with Behringer or Music Tribe. It exists because Music Tribe, Behringer's
parent company, published [PRO800-Firmware-Packer](https://github.com/MusicTribeOpenSource/PRO800-Firmware-Packer):
the main-board
[schematic](https://github.com/MusicTribeOpenSource/PRO800-Firmware-Packer/blob/main/Schematics/PRO800_Main_Schematics.pdf),
the chip's memory layout, and the packer that turns a firmware image into an update the bootloader accepts.

> [!WARNING]
> **Wildly experimental.** A hobby project, built and tested on a single synth. It replaces the PRO-800's firmware,
> drives its analog circuits directly and rewrites its patch memory. Use it entirely at your own risk: it comes as
> is, without warranty of any kind (see [LICENSE](LICENSE)), and the authors accept no liability if it harms your
> synth, your patches or anything connected to it. Flashing unofficial firmware may also void the synth's warranty.
> Back up your patches first, and know the way back:
> [Updating, recovery and going back to stock](#updating-recovery-and-going-back-to-stock).

PRO-8000 replaces the stock PRO-800 firmware. Same front panel, different engine:

- two layers (split, stack or by MIDI channel);
- two LFOs per layer with nine shapes (a programmable step pattern and noise among them), clock sync and per-voice
  retrigger;
- four envelopes per voice (ADSR, DAHDSR or cycling for a count of cycles, from key down or key up, timed in seconds
  or note values, three curves);
- a 16-route mod matrix per layer;
- a step sequence per patch, played from a key and transposed by it, with up to four automation lanes;
- autotune that keeps every oscillator within about a cent;
- a browser editor.

The stock sequencer and arpeggiator are gone; their buttons do other jobs now.

## The panel at a glance

| Control | What it does now |
|---|---|
| Knobs | As printed, with a few changes. LFO FREQ / LFO AMOUNT edit LFO 1 or LFO 2. With Shift (Sync Source) held, the filter ADSR knobs edit Env 3, the amp ADSR knobs Env 4, and the other knobs secondary values (see Shift). |
| FREQ A / FREQ B | Their tuning mode is per oscillator and layer; hold Seq 1 or Seq 2 while turning a FREQ knob to cycle its mode (`SEnI` semitones, `OCt` octave, `FrEE` free, `FIHd` fixed; a quarter turn a step). |
| FINE | Oscillator B, ±100 cents, centre = in tune; the display shows cents. |
| Keyboard-tracking switch | Keyboard tracking, as printed: off, half, full. A loaded patch's setting holds until you move the switch. |
| Sync Source | **Shift**: hold it, or press it twice quickly to latch it (LED on; twice again to release). |
| Sync Clock | The clock: tap the tempo (its LED blinks on the beat of whichever clock is in use), hold it to restart the bar, Shift + press to cycle the clock source. |
| LFO Shape button | Steps to the next shape at every press: `trI`, `PULS`, `SA.UP`, `SA.dn`, `SInE`, `S-H`, `drFt`, `StEP`, `nOIS`. |
| VALUE knob | Changes the setting on the display: patch number, menu value, mod depth, or the shape just after pressing LFO Shape. |
| Seq 1 / Seq 2 | Choose the layer the knobs edit (LED); hold one while turning FREQ A or B to change that oscillator's tuning mode. |
| Arp Up-Dn | Choose LFO 1 or LFO 2 for the LFO knobs, the shape button and the LFO routing buttons (LED lit = LFO 2). |
| Arp Assign | MOD ASSIGN: the mod matrix (LED blinks while assigning). |
| Preset | Preset mode (a patch loaded) ↔ manual mode (the panel is live). |
| Rec | Save (blinks, choose the slot, Rec again). Hold Rec and press Seq 1: record sequence steps from MIDI (`rSEq`, see Sequencer). |
| Perf | Load the init sound: one sawtooth, filter open. |
| Settings | Opens the settings menu (keys 1-5). |
| Tune | Autotune (about 25 s, silent). |

The display shows a knob's value while you turn it, in real units where it has one:

| Knob | Shows | Range and curve |
|---|---|---|
| FREQ A / B | depends on tuning mode | semitones: ±36 in steps; octave: ±3 octaves; free: continuous ±36 semitones; fixed: C1-C8 |
| FINE | cents | ±100 |
| M TUNE | Macro 1, signed (`-45`, `  12`) | −99..99, 0 in the middle |
| CUTOFF | Hz; with a point from 1 kHz on, kHz (` 737`, ` 1.24`, ` 16.7`) | 16 Hz-16.7 kHz, exponential |
| FIL ENV AMT | semitones, signed (`-4.5`, `  18`) | ±72, squared: fine near the centre (25 % from the centre = 4.5, half = 18) |
| PMOD FIL ENV | semitones (` 3.00`, ` 12.0`) | 0-48, squared (a quarter = 3, half = 12) |
| LFO FREQ | Hz (` 1.60`), or the clock division when synced | 0.05-51 Hz, exponential |
| GLIDE | time constant (` 150` ms, ` 1.25` s), `OFF` at the bottom | 2 ms-2 s |
| Envelope times | ms / s (see Envelopes) | |
| PW A / B | 0-99 | the pulse is high for 5-95 % of the cycle, 50 a square; LFO → PW and routes can push it to silence at either end |
| everything else | 0-99 | |

Each layer stores a separate mode for FREQ A and FREQ B. The modes are **Semitones** (the original ±36-semitone
range in one-semitone steps), **Octave** (−3 to +3 octaves in octave steps), **Free** (continuous ±36 semitones),
and **Fixed** (continuous C1-C8, independent of played notes). Fixed mode still responds to pitch bend and pitch
modulation. Its display shows the equivalent frequency in Hz or kHz. Mode changes are saved with the patch.

A digit display can't show a plus sign, so positive values have none. A dot after the value means the knob hasn't
**picked up** the stored value yet. Turn it past the stored value (or onto it) and it takes over. This happens:

- after loading a patch;
- after switching layer, LFO or envelope;
- after editing from the web page.

**Pickup off** (Shift + FIL ENV AMT, `P.UP` → `OFF`; or the web page's System tab, or `pro800 set pickup off`): a
knob waiting like this takes over as soon as you move it, and the value jumps to where the knob is, as on the stock
firmware. The dot still shows until you move it. It's a system setting that survives power-off; pickup is the default.
Either way, a knob that was busy with something else (under Shift, in MOD ASSIGN, on the envelope page, during Seq +
FREQ) waits from where it is when that ends, so letting go moves nothing; move it after and it takes over.

## Playing

Play over USB MIDI or the 5-pin MIDI IN socket, on any channel, or on one: Shift + NOISE (`CHnL`) sets the MIDI
channel, `ALL` (the default) or 1-16, also on the web page (Sound tab, by CC A / B) and with `pro800 set midi-channel`.
It's a system setting, kept when you load patches. In the layer modes that play by channel it's the first of their
channels: channel mode plays layer 1 on it and layer 2 on the next, `8CH` voices 1-8 on it and the seven after,
`nPE` takes it as the master and the eight after as the members (set 1, these are the channels in Layers below).
Notes, controllers, bend, pressure and program changes on other channels are ignored; clock and SysEx aren't. Pitch bend is ±2 semitones by default (Settings key 7, per patch). The mod wheel adds LFO 1 depth and is also a mod
source. Aftertouch is a mod source too, and it's per voice: poly pressure (the Move's pads send it) moves only the
voice playing that note; channel pressure moves them all. In channel mode (`CHAn`) each channel's bend, mod wheel,
pressure and sustain pedal reach only the layer it plays; in the `8CH` and `nPE` modes each channel's bend, pressure
and CC 74 reach only its voice (see Layers). CC 74 is the `Timbre` mod source. The firmware follows MIDI clock
(Start, Stop, Continue, Song Position and Clock) for LFO sync.

The 5-pin MIDI IN socket (from 0.7.6) takes the same notes, controllers, pressure, bend, program changes and clock,
so a keyboard with a MIDI out plays the synth without a computer. SysEx (the web page, the tool, updates) needs USB.
The USB socket is a device port, so a USB-only keyboard needs a computer or a USB MIDI host box in between (or the
web page's MIDI router).

- **Sustain pedal** (CC 64): keys let go while it's down keep sounding (and keep a sequence running) until it's
  released.
- **Program change** 0-127 picks patch 1-128 (an empty one: the init sound). Bank select is ignored.
- **Controllers** set knobs on the layer the channel plays (layer 2 for channel 2 in channel mode), else the layer
  you're editing; the knob then picks up as after loading a patch:

| CC | Knob | CC | Knob | CC | Knob |
|---|---|---|---|---|---|
| 5 | Glide | 21 | Noise | 29 | PMOD filter env |
| 14 | FREQ A | 22 | Filter env amount | 30 | PMOD osc B |
| 15 | FREQ B | 23-26 | Filter A / D / S / R | 31 | M TUNE (Macro 1) |
| 16 | FINE | 27 / 28 | LFO 2 rate / amount | 70 / 72 / 73 / 75 | Amp S / R / A / D |
| 17 / 18 | PW A / B | 71 | Resonance | 76 / 77 | LFO 1 rate / amount |
| 19 / 20 | Level A / B | 74 | Cutoff | 85-88, 89-90 + 102-103 | Env 3, Env 4 A / D / S / R |

### Voice modes, drift, scale, morph

- **Voice mode** (per layer; Settings key 9 `UOIC`, or the web page): `POLY`, mono (`nono`: one voice, every new
  note retriggers the envelopes) or legato (`LEG`: one voice, retriggers only after all keys are up). Note priority
  (web page): last, low or high note; it also applies to unison, which plays the same line on all the layer's
  voices. In the two-layer modes, a layer in mono or legato (without unison) takes one voice and the other layer
  gets the other seven (from 0.8.3; otherwise four each).
- **Drift** (Settings key 0 `drFt`, 0-99): each voice's pitch (up to 15 cents) and cutoff (up to a semitone) wander
  slowly at random, so stacked notes and unison breathe like an old poly.
- **Scale** (web page: scale and root): the pitch of each voice, with its common pitch modulation (LFO → Freq, LFO
  2 → pitch, routes to Pitch A + B), snaps to the scale. An S&H or drift LFO on pitch plays melodies; a sequence's
  transpositions stay in key. Keys off the scale snap too. Major, minor, Dorian, Phrygian, Lydian, Mixolydian,
  harmonic minor, pentatonic major / minor, blues, whole tone, chromatic.
- **Morph** (web page): pick another patch as the morph target and a source to morph by (a route to the Morph
  destination). The source fades every knob from this patch's value to the target's: wheel up for a different
  sound, an LFO for a slow evolution, a sequencer lane for a different sound on every step, velocity for harder
  sounds on harder notes. Buttons, routes and settings stay this patch's.

## Patches

There are 128 patches, shown as `P  7` or `P128`. Each has its own step sequence, kept with it (from 0.8.0; the
patches saved by 0.7.x firmware are wiped at the first start of 0.8.0, since their records moved; the EEPROM backups
keep them).

- **Load:** in preset mode (Preset LED on), type the number: a third digit, or a pause of 3 seconds, or a number
  that no further digit could keep within 1-128 ends it (`7` then `2` loads P72 at once; `1`, `2` waits for a third
  digit). Or turn VALUE to step through them. An empty slot loads the init sound (the display keeps the slot number), ready to
  build and save there with Rec, Rec (Shift + Perf brings back the sound before). Boot loads the last patch.
- **Edit:** a loaded patch's knobs are in pickup (with pickup off: until you move them) and its buttons keep their
  stored state until you press them.
  Press Preset for manual mode, where the whole panel is live as it is.
- **Save:** press Rec (it blinks), choose a slot by number or with VALUE, then press Rec again (`donE`). You don't
  have to wait after typing: `5`, Rec saves to P5 at once. Preset cancels. The patch's name follows the sound into an empty slot.
- **Init:** Perf loads a plain sound. Rec saves it anywhere.
- **Names, export, backup, the factory bank:** on the web page (Patches tab).
- **Filling the slots after installing:** the firmware starts with no patches of its own, and slots that still hold
  the stock firmware's data show `nonE`. Saving a patch or installing a bank is enough for the synth to start on your
last patch from then on; the other slots stay as they are. On the web page's Patches tab: **Format the patch memory** (every slot
  empty), then **Copy the whole factory bank** (the 100 stock sounds into the empty slots, converted) and/or
  **Install the preset bank** (66 sounds into P1-P66; or **Install a bank file…** with the release's
  `pro8000-presets-<version>.json`). Each asks first.

A patch stores:

- both layers' knobs, Env 3 / 4 and LFO 2 rate and amount;
- the latching buttons and the keyboard tracking;
- the layer mode and split;
- both LFOs' sync, retrigger, shapes and LFO 2 routing;
- both layers' mod routes;
- the name;
- its step sequence and automation lanes.

M VOL is never stored.

## Layers

Settings → key 3 (`LAYr`) has six modes:

| Mode | Voices | Notes |
|---|---|---|
| `OFF` | Layer 1 on all 8 voices | — |
| `SPLt` | 4 each (7 + 1 with one layer mono), or as set below | Layer 1 below the split note, layer 2 from it up. Set the note with Settings → key 4, VALUE, or by playing it while that page shows. |
| `StAC` | 4 each (7 + 1 with one layer mono), or as set below | Both layers on every note. A sequence on one layer's keys plays that layer while the other plays the keys: a pad with an arpeggio on top. |
| `CHAn` | 4 each (7 + 1 with one layer mono), or as set below | MIDI channel 2 plays layer 2, every other channel layer 1. |
| `8CH` | Layer 1, one voice per MIDI channel | Channels 1-8 each play one voice, mono, with their own pitch bend (the bend-range setting), pressure and CC 74. Channels 9-16 play nothing. For sequencers that drive eight mono lines. |
| `nPE` | Layer 1, one voice per member channel | MPE, lower zone: channel 1 is the master (its bend, mod wheel, pressure and pedal reach every voice, its notes play polyphonically), channels 2-9 each play one voice with 48 semitones of bend, their own pressure and CC 74. The usual MPE controller default; the firmware doesn't read the RPNs that change it. |

**Voices per layer** (0.9.0): on the `LAYr` page, Arp Up-Dn switches to `Lr.Uo`, how split, stack and channel
mode share the eight voices: VALUE chooses `AUto` (4 + 4, or 7 + 1 with one layer mono or legato) or layer 1's
count, `1-7` … `7-1` (layer 1 - layer 2); Arp Up-Dn goes back. A stack of a 6-voice pad and a 2-voice lead is
`6-2`. Changing it releases every note. It's stored in the patch; the web page has it as Voices next to Layers.

In both per-channel modes the mod matrix's `Timbre` source is that voice's CC 74, and `Aftertouch` its channel
pressure, so a slide or a press shapes each note on its own.

Seq 1 / Seq 2 choose the layer the knobs edit (`L  1` / `L  2`).

**Links** (0.9.3): in split, stack and channel mode, layer 2 can follow layer 1, so a stack of two oscillator sounds
shares one filter sweep or one vibrato. The groups are the filter (CUTOFF and FIL ENV AMT), each of the four
envelopes (knobs, mode, trigger, curve, delay, hold, cycles, sync, timing) and each LFO (rate, amount, shape, step
pattern, LFO 2's routing). Set them with the Link buttons in those sections of the web page, or `pro800 set link`.
They're stored in the patch. While a group is linked:

- Layer 2 plays layer 1's values. Its own stay stored and come back when you unlink.
- Turning one of its knobs on layer 2 edits layer 1, and both layer LEDs light for a second to say so. So do the
  envelope pages in Settings and the LFO Shape button.
- A linked LFO is layer 1's LFO itself, so the layers stay in phase; in per-voice retrigger each voice keeps its own
  phase at that rate.
- Mod routes stay per layer, so layer 2's routes still add to the shared values.

**Per layer:**
- FREQ A/B and FINE;
- CUTOFF and FIL ENV AMT;
- all four ADSRs;
- GLIDE and M TUNE (Macro 1's knob);
- both LFOs: rate, amount and shape, plus LFO 2's routing;
- PMOD FIL ENV;
- the mod routes.

**Shared,** because the hardware has a single wire for these or a button can show only one state:
- the mixer levels and noise;
- resonance;
- PW A and PW B;
- PMOD OSC B;
- M VOL;
- every button, and LFO → PW, which comes from layer 1's LFOs.

## LFOs

Each layer has two LFOs. **Arp Up-Dn** chooses which one you're editing (display `LFO1` / `LFO2`, LED lit =
LFO 2). The selection applies to:

- LFO FREQ and LFO AMOUNT (with pickup);
- the LFO Shape button;
- the LFO → Freq / Filter / PW buttons;
- the Sync and Retrigger settings pages.

**Shapes:** press LFO Shape to step to the next one. For 2 seconds afterwards, VALUE steps through them both
ways. The button's up/down position no longer means anything.

**Delay and fade-in** (web page, LFO sections, or `pro800 set lfo-delay|lfo-fade 1|2 <0-99>`): each LFO can wait
after a note starts and then fade in, per voice, from 20 ms to about 5 s (0 = off). It applies to the LFO's
routing buttons, its mod routes and LFO → PW alike, and restarts with each new note on the voice (legato keeps it).
The setting is per LFO, shared by both layers, and saved in the patch.

| Display | Shape |
|---|---|
| `trI` | Triangle |
| `PULS` | Square |
| `SA.UP` | Ramp up (saw) |
| `SA.dn` | Ramp down |
| `SInE` | Sine |
| `S-H` | Sample & hold: a new random step every cycle |
| `drFt` | Drift: smooth glides between random values |
| `StEP` | Steps: a pattern of 1-16 on/off steps, edited on the web page |
| `nOIS` | Noise: a new random value 16 times a cycle, glided to: a rough wander at low rates, a fast jitter at the top (up to about 800 values a second) |

### Step patterns and trance gates

The `StEP` shape plays a pattern of up to 16 steps over one LFO cycle: high for a step that's on, low for one
that's off. Each LFO of each layer has its own pattern, stored in the patch. The default is the trance gate
`1 0 1 0 1 1 0 0`, twice. Edit it on the web page's Sound tab: choose Steps as the LFO's shape and a row of step
boxes appears. Click steps on or off, choose 2-16 steps, and use Clear, Invert, rotate ◀ ▶ or Trance gate. On the
command line: `build/pro800 set steps <layer> <lfo> x.x.xx..`.

For a gate on the volume:

1. Give the LFO the `StEP` shape.
2. Route it to the VCA at depth 99, with the web page's "Route to VCA" button or MOD ASSIGN (key 1, touch AMP SUS,
   VALUE to 99). Lower depths only dip the volume instead of cutting it.
3. Set the rate. With MIDI clock (Sync `CLoC`), one cycle is the whole pattern: 16 steps at `1bAr`, or 8 steps at
   `1-2`, are 16th notes. With retrigger `EACH` the pattern starts with every chord.

Every change is softened over 8 ms so the gate doesn't click. The pattern works on any destination too: steps to
the filter, or to pitch for octave jumps (LFO → Freq at full depth is ±12 semitones).

**Rate:** LFO FREQ, 0.05-51 Hz. **Depth:** LFO AMOUNT, on a squared curve so small values are fine. The mod wheel
adds to LFO 1's depth.

**Routing:** for LFO 1, the LFO → Freq / Filter / PW buttons work as printed. For LFO 2, select it and press the
same buttons. The display shows the result, e.g. `Fr.on` or `FL.oF`. LFO 1's routing stays as it was, and the
buttons' positions keep showing LFO 1's routing. LFO → PW is one wire, so it takes layer 1's LFOs. Pitch ±12
semitones and filter ±48 at full depth.

**Sync** (Settings → key 1, `SYnC`, or `SYn2` for LFO 2): `FrEE` or `CLoC`. With the clock, LFO FREQ chooses a
division (shown while turning): `8bAr`, `4bAr`, `2bAr`, `1bAr`, `1-2`, `1-4d`, `1-4`, `1-4t`, `1-8d`, `1-8`,
`1-8t`, `16d`, `1-16`, `16t`, `1-32`. MIDI Start lines the LFO up with the bar; Continue picks up where Song Position
put it (or where the clock stopped). Until a clock arrives, `CLoC` runs the division at 120 BPM.

**Retrigger** (Settings → key 2, `trIG` / `trI2`):

| Display | Mode |
|---|---|
| `FrEE` | Free-running; with the clock, the phase follows the grid |
| `1St` | Restarts on the first key after all were released |
| `EACH` | Restarts on every key |
| `PoLY` | Every voice runs its own LFO from its note-on: per-voice LFOs |

Sync and retrigger are set per LFO (LFO 1, LFO 2) and apply to both layers.

## Envelopes

Every voice has four ADSRs: filter, amp, Env 3 and Env 4. With **Shift** (Sync Source) held or latched, the four
filter ADSR knobs edit Env 3 and the four amp ADSR knobs Env 4, with pickup; without it they edit the filter and
amp envelopes. Env 3 and Env 4 do nothing until a mod route uses them. Keyboard tracking is the switch again
(`OFF` / `HALF` / `FULL`; a loaded patch's value holds until the switch moves), also Settings → key 5 (`trAC`).

While you turn a time knob the display shows the time: milliseconds as a whole number (` 150`), seconds with a
point (` 1.25`, ` 12.5`).

| Knob position | 0 | 10 % | 25 % | 50 % | 75 % | 100 % |
|---|---|---|---|---|---|---|
| Attack | instant | 3 ms | 17 ms | 150 ms | 1.25 s | 10 s |
| Decay, release | 3 ms | 9 ms | 37 ms | 310 ms | 2.5 s | 20 s |

Decay and release times are how long the fall takes to reach −60 dB, so a release showing 2 s is gone at 2 s.
The attack has the concave shape of an analog envelope: it shoots up first and eases into the top. Decay and
release fall exponentially. Sustain is a level, 0-99. The engine's pass takes about 1.2 ms with one voice sounding
and about 2.4 ms with all eight (measured on the synth), and within it the VCAs get a new value four times and the
cutoffs twice: with eight voices the VCAs move about every 0.6 ms and the cutoffs about every 1.2 ms, with one voice
every 0.3 and 0.6 ms, so attacks below that sound like an instant attack. MIDI is read and the sequence advanced
before every one of those four steps, so a note starts within about 0.3-0.6 ms of its message. The LFOs and the routes from envelopes and
LFOs into pitch, cutoff and the VCA move at that rate too; each voice's pitch goes out once a pass with its value at
that moment, and the knobs and other routes update once a pass.

### Envelope modes, triggers, curves, delay and hold

Settings → key 6 sets one envelope: `En.A` (amp), `En.F` (filter), `En.3` or `En.4`. It opens on the envelope the
filter ADSR knobs edit, or amp; press 6 again for the next one. Arp Up-Dn steps through the envelope's five values,
and from the last back to the first:

| Page | VALUE |
|---|---|
| `En.A` … mode | `AdSr` (ADSR, the default), `dAHd` (DAHDSR: a delay before the attack and a hold at the top before the decay), `CYCL` (cycling on its own times, below), `8bAr` … `1-32` (synced cycling, below) |
| `tr.A` … trigger | `dn` (from the key going down, the default) or `UP` (from key up), in any mode |
| `tI.A` … timing | `SEC` (the time knobs in seconds, the default) or `SYnC` (in note values at the tempo, below) |
| `CU.A` … curve | `AnLG`, `LIn`, `SUEL` |
| `Cn.A` … cycles | `LOOP` (endless, the default) or `1` … `99`: how many cycles a cycling envelope runs |

- **Delay and hold:** while page 6 is open, the ATT knob sets the envelope's delay (`dLY`) and the DEC knob its hold
  (`HoLd`), each 0-10 s on the attack's curve, by how far you turn them; the display shows the time. Meanwhile those
  knobs leave the attack and decay alone (they pick up again after). They count in the `dAHd` modes only. During
  the delay the level goes on falling as in a release, so a retriggered note doesn't click.
- **Key up** (`UP`): while the key is down the envelope rests at zero. When you let go it runs its delay, attack and
  hold, then the decay for its time towards the sustain level, then the release. A new note on that voice fades it
  with the release time. Use it for a sound that starts when you lift your finger: a key-up amp envelope plays only after
  the key (a release "thump"), a key-up Env 3 → CUTOFF opens the filter as a note ends. With sustain at 0 it's an
  attack-decay blip; with sustain up, DEC is how long it holds there before the release.
- **Timing** (`tI.A` …): with `SYnC` the envelope's time knobs step through note values instead of seconds: `0`,
  then `1-32`, `16t`, `1-16`, `16d`, `1-8t`, `1-8`, `1-8d`, `1-4t`, `1-4`, `1-4d`, `1-2`, `1bAr` … `8bAr` (the
  synced LFO's divisions), at the clock's tempo (the MIDI clock, the internal tempo or SYNC IN; 120 BPM until a MIDI
  clock arrives). Turning one shows its note value. That's the attack, decay and release (sustain stays a level), the
  delay and hold, and while it cycles, the attack, hold, fall and rest. Times follow tempo changes. Synced cycling
  (a division in the mode) keeps its own scheme, where the knobs share out one division; the timing setting doesn't
  apply to it. A decay or release in note values is the time to fade away (-60 dB), like the seconds.
- **Curve** (`CU.A` …): `AnLG` (analog: the
  concave attack and exponential decay and release described above, the default), `LIn` (linear: straight lines;
  each stage covers full scale in its time, so a release from half way takes half of it) or `SUEL` (swell: an attack
  that starts slowly and rises faster, the analog decay and release).

All of these are per layer and stored in the patch. On the web page, each envelope has a Mode menu, a Trigger menu
(Key down / Key up), a Timing menu (Seconds / Note values), a Curve menu, Delay and Hold knobs in DAHDSR mode and a
Cycles knob while it cycles.

In 0.9.0 key up was a mode of its own (`UP.AS`, `UP.dA`) and cycling ignored it. Patches saved by 0.9.0 load as
they sounded: a cycling envelope's key-up trigger is cleared when such a patch loads.

### Cycling envelopes

Any envelope can cycle while the key is held, like a one-shot LFO per note that restarts itself: on page 6 choose
`CYCL` (cycling on its own times) or a clock division (`8bAr` … `1-32`, synced cycling, below). DAHDSR's delay and
hold don't apply while it cycles; the curve and the trigger do.

While it cycles, the knobs mean:

| Knob | In a cycle |
|---|---|
| ATT | Attack: the rise to the top (0-10 s) |
| SUS | Hold: time at the top (0-10 s) |
| DEC | Fall: the drop to zero (3 ms-20 s) |
| REL | Rest: time at zero before the next attack (0-10 s) |

On key up the envelope falls to zero with the DEC time. One cycle lasts attack + hold + fall + rest.

**Count** (`Cn.A` …): `LOOP` cycles for as long as the key is held. A number runs that many cycles and then
rests at zero until you let go, however long you hold: `1` is a one-shot attack-hold-fall, `3` a triple hit.

**From key up** (`tr.A` … `UP`): the cycles start when you let go of the key, and the envelope rests at zero while it
is down. With a count, it runs that many cycles and ends, so a key-up cycling amp envelope plays a burst of repeats
after each note (an echo-like tail; the rest sets the gap between them). With `LOOP` it goes on cycling until the
next note on that voice fades it, or the voice is taken for another note: give an amp envelope a count unless you
want notes that never end.

Some ideas:

- A cycling amp envelope with short attack and fall makes rhythmic gating that restarts with every note.
- A cycling Env 3 → CUTOFF gives each note its own filter "wah" pattern.
- Cycling Env 4 → pitch A with a long rest gives a recurring blip.
- An amp envelope synced to `1-16`, count 4, from key up: four sixteenth-note repeats after each note.
- Env 3, count 1, from key up, → CUTOFF or pitch: one shaped blip as each note ends.
- `CYCL` with timing `SYnC`: attack `1-16`, hold `0`, fall `1-8`, rest `1-4` is a cycle of a half note with each
  stage on the grid; unlike synced cycling, each stage keeps its own note value as you change the others.

### Synced cycling

With a clock division instead of `FrEE`, one cycle lasts that division at the MIDI clock's tempo (120 BPM when no
clock has arrived). The four knobs become weights that share the cycle out: attack, hold (SUS), fall (DEC) and rest
(REL) in proportion. Turning one up takes time from the others, and REL at 0 means no gap. While you turn one, the
display shows its share of the cycle in percent; the web page shows all four.

The cycle restarts with each key and follows tempo changes, so it stays on the beat however long you hold. A
synced amp envelope at `1-16`, with a short attack, some hold and a longer fall, is a pulsing gate with shaped
edges. On key up it falls with its share of the cycle. A count counts whole cycles of the division.

## Sequencer

Each patch has a step sequence that plays from a key. Edit it on the web page (Sound tab, Sequencer); the panel
only plays it.

- **Playing:** with the sequence on, pressing a key starts it at step 1, transposed so that a step of 0 plays that
  key. It loops while keys are held (or plays once per key). Pressing another key while holding transposes it
  from the next step without restarting. Letting go of every key stops it. The notes go through the normal
  voices, envelopes and modulation.
- **Steps:** up to 60. Each is on or a rest, with a note offset (semitones from the key), a gate (1-99 % of the
  step; 100 % ties into the next step, legato), a velocity (0 = the key's), a probability (100 % down to 12.5 %,
  in eighths: some steps sometimes don't play) and ratchets (the step plays as 2, 3 or 4 quick notes).
- **Arpeggio:** instead of transposing, the steps can walk the held notes (up, down, up-down, random, or in the
  order played); each step's note offset transposes the arpeggio note (+12 for an octave jump). A chord played
  within 30 ms counts as one.
- **Timing:** a step lasts a clock division (1/16, 1/8T, …) at the MIDI clock's tempo (120 BPM until a clock
  arrives), or at the sequence's own BPM; swing makes even steps longer and odd ones shorter.
- **Layers:** the sequence can take keys of both layers, or only those of layer 1 or layer 2. In a split, one hand
  plays a sequenced bass and the other plays normally. In a stack, the sequence plays its layer and the other
  layer plays the keys as usual (from 0.8.3; before, the other layer stayed silent): for a pad with an arpeggio
  on top, put the pad on layer 1 (poly), the arp sound on layer 2 in mono, and the sequence on layer 2's keys
  with an arp mode. The pad then has seven voices and the arp one.
- **Automation lanes:** up to four, one value per step (−99..+99), optionally gliding from step to step. They're
  mod sources Seq A-D: choose a destination next to each lane (a route at depth 99 on the layer you're editing) or
  route them in the Mod matrix with any depth. Steps and lanes share the space in the sequence's slot: 60 steps
  with no lanes, 48 with 1, 40 with 2, 34 with 3, 30 with 4.
- **Piano roll (0.9.3):** above the steps on the web page, the current page's notes over their step columns, three
  octaves at a time with a keyboard beside them and ▼ ▲ to scroll by an octave. Offsets are from the key you hold, so
  offset 0 is drawn as C and the Cs are labelled in semitones.
  - **Add:** click. A rest becomes that note, and on a played step the note goes beside what's there. When a step needs
    another note the Chords menu goes up by itself, as long as the sequence still fits; otherwise, with one note a step, the
    note moves there, or a full step says so.
  - **Move:** drag a note up or down. A note held over several steps moves as one.
  - **Stretch:** drag a note's right edge. Inside its step this sets the step's gate, shared by the notes that aren't held.
    Past its step the note holds and joins the next steps beside their notes, so a long bass can run under short notes; dragging
    back restores what was there. A step with no room stops the stretch. In the last step, a note alone ends where you let
    go; beside other notes it lasts to the step's end.
  - **Remove:** double-click or right-click a note. If it was the step's own note, a chord note takes over.

  A longer sequence also gets a thin strip showing all of it.
- **Chords (0.9.3):** a step can play up to three notes. Choose Two or Three notes under Chords on the web page, and
  each step gets a Note 2 / Note 3 cell, in semitones from the step's own note (`·` = no extra note, so a step can
  have fewer). Each extra note takes a lane's room, so three notes alone leave 40 steps and at most two automation
  lanes (30 steps). The notes share the step's velocity, probability and ratchets, and its gate unless a note holds. A
  held note ignores the gate and carries on into the next step, without restarting if that step plays it too (the piano roll sets
  this when you stretch a note). A gate of 100 % (`tie`) holds all of the step's notes. Ratchets switch holds off. They use real voices: a chord on a 4-voice layer leaves little room for
  overlap, and a mono or legato layer plays only the last of them. In an arpeggio, the extra notes follow the arp's
  note. Step recording writes single notes.
- **Recording on the panel (0.9.0):** hold Rec and press Seq 1 (`rSEq`; the edit layer stays, and REC blinks fast).
  Each MIDI note you play is the next step from step 1 (`St 3`): its offset from the first note, its velocity, a
  50 % gate. Seq 2 writes a rest. Rec or Seq 1 ends it (`donE`); `FULL` when the steps run out. The length follows,
  the sequence is switched on, and its lanes and settings stay. The notes sound while you record, without the
  sequence. Rec then saves it with the patch as usual.
- **Resizing on the web page (0.9.3):** **Duplicate** repeats the pattern, doubling the number of steps; it's greyed out when they
  wouldn't fit what the lanes and chords leave. **Halve** keeps the first half of the steps as they are and drops the rest (an odd
  length rounds up). The step length stays the same for both.
- **On the web page also:** Euclid fills the steps with k hits spread evenly over the length; Record writes notes
  you play (the on-screen keys or the routed MIDI input, e.g. the Move) into the steps one by one, relative to the
  first; Reset steps.
- **Saving:** the sequence is saved with the patch (REC, or Save in Patches) and loaded with it. PERF's init sound
  has none.

## Mod matrix (MOD ASSIGN)

Each layer has 16 routes. A route adds source × depth (−99 to 99) to a destination.

1. Press **Arp Assign** (LED blinks, display `SrC`). The knobs go into pickup and stay there until you leave MOD
   ASSIGN, so touching one won't change the sound.
2. **Pick the source:** a number key (table below), or touch a knob that stands for one: LFO FREQ or LFO AMOUNT
   for the selected LFO, a filter ADSR knob for the filter envelope, an amp ADSR knob for the amp envelope (with
   Shift held: Env 3 and Env 4). Or turn **VALUE** to scroll through every source, the web-only ones included
   (one per click cycle, as patches scroll), and press **Arp Assign** to take the one shown (touching a destination
   takes it too). VALUE keeps scrolling sources until one is taken. A knob that picks the source keeps picking it
   while you turn it; it can be the destination too (an envelope into its own stage) once it has rested a second.
3. **Pick the destination:** turn its knob a little, or press its LFO routing button. Or scroll with VALUE (the
   display shows only the destination's name) and press Arp Assign to take the one shown. The envelope stages are
   reached by touching the ADSR knobs (with Shift held: Env 3 on the filter knobs, Env 4 on the amp knobs); AMP SUS
   stays the VCA unless Shift is held. A knob you touched in the 5 seconds before pressing Arp Assign is already
   the destination: tweak a knob, press Arp Assign, pick the source and you're at the depth.
4. Turn **VALUE** to set the depth for the edit layer. 0 removes the route; `FULL` means all 16 are in use.
5. Press **Arp Assign** again to finish. The display goes back to the patch number.

**Browse:** hold Arp Assign for half a second (LED steady). Turn VALUE to step through the edit layer's routes by
number, `nod1` to `nod9`, then `no10` to `no16` (n stands in for m on the display). When VALUE rests, the route
shown cycles through its number, source, destination and depth. A short press opens the route shown for editing;
holding Arp Assign again leaves, back to the patch number. If the layer has no routes yet, the hold starts MOD
ASSIGN (`SrC`) as a press does.

Touching a destination shows its current depth. Seq 1 / Seq 2 and Arp Up-Dn still work while assigning.

**Macros:** `nAC1` and `nAC2` are destinations and sources at once. The M TUNE knob is Macro 1's own knob (0.9.0;
master tune is gone, autotune keeps the voices in tune): its position, −1 to 1 with 0 in the middle, adds to the
macro along with its offset (Shift + CUTOFF) and whatever is routed into it, so Macro 1 → anything is a hand knob
for those destinations. Route anything into a macro (several things, if
you like; the sum is the macro's value, −1 to 1), then route the macro to as many destinations as you want, each
with its own depth and sign: mod wheel → Macro 1, Macro 1 → cutoff 40, resonance 20, A pulse width −30. The web
page's Mod matrix shows each macro's inputs and outputs on one line. A macro is worked out once a pass (1-2.4 ms),
so for a fast LFO straight into the cutoff route the LFO directly.

| Key | Source | Display |
|---|---|---|
| 1 | The selected LFO; press 1 again for the other one | `LFO1` / `LFO2` |
| 2 | Filter envelope | `FEnV` |
| 3 | Amp envelope | `AEnV` |
| 4 | Velocity | `VEL` |
| 5 | Mod wheel | `nod` |
| 6 | Aftertouch | `PrES` |
| 7 | Env 3 | `EnV3` |
| 8 | Env 4 | `EnV4` |
| 9 | Random, new for every note | `rAnd` |
| 0 | Note number (centred on middle C) | `notE` |
| VALUE / web | Sequence lanes A-D | `SEqA`… |
| VALUE / web | LFO 1 / LFO 2 as 0-to-1 (bottom to top, for things that should only open) | `L1.U` `L2.U` |
| VALUE / web | Gate: 1 while the key is down, 0 in the release | `GAtE` |
| VALUE / web | Timbre, CC 74 (per voice in the per-channel modes) | `tInb` |
| VALUE / web | Pitch bend, −1 to 1 | `bEnd` |
| VALUE / web | Sustain pedal, 0 or 1 | `PEdL` |
| VALUE / web | CC A, CC B: two controllers, numbers set on the web page or `pro800 set cc-a|cc-b` (11 and 2 by default) | `CC.A` `CC.b` |
| VALUE / web | Voice number, 0 to 1 across the layer's voices: a different offset on every voice | `VoIC` |
| VALUE / web | Fixed, always 1: the route is a plain offset | `FIX` |
| VALUE / web | Macro 1, Macro 2 | `nAC1` `nAC2` |

The Morph destination (web page only) fades toward the morph target patch (see Voice modes, drift, scale, morph).

| Touch | Destination | Display | Full depth |
|---|---|---|---|
| FREQ A | Pitch A | `P.A` | ±24 semitones ‡ |
| FREQ B or FINE | Pitch B | `P.b` | ±24 semitones ‡ |
| M TUNE | Macro 1 | `nAC1` | ±1 |
| CUTOFF | Cutoff | `CUt` | ±96 semitones |
| FIL ENV AMT | Filter envelope amount | `F.EA` | full scale |
| PMOD FIL ENV | Poly-mod envelope amount | `P.EA` | full scale |
| AMP SUS | VCA gain † | `VCA` | closed to full |
| LFO AMOUNT | LFO 1 / 2 depth | `L.An` / `L2.An` | full scale |
| LFO FREQ | LFO 1 / 2 rate | `L.rt` / `L2.rt` | ±4 octaves |
| LFO → Freq button | LFO 1 / 2 → pitch depth | `L.Fr` / `L2.Fr` | full scale |
| LFO → Filter button | LFO 1 / 2 → filter depth | `L.FL` / `L2.FL` | full scale |
| LFO → PW button | LFO 1 / 2 → PW depth * | `L.PU` / `L2.PU` | full scale |
| A LEVEL / B LEVEL / NOISE * | Mixer | `A.LE` `b.LE` `noIS` | full scale |
| RESONANCE * | Resonance | `rES` | full scale |
| PW A / PW B * | Pulse width | `A.PU` `b.PU` | ±50 % |
| PMOD OSC B * | Poly-mod oscillator B amount | `P.ob` | full scale |
| AMP ATT / DEC / REL | Amp envelope attack / decay / release time | `EA-A` `EA-d` `EA-r` | 16× shorter or longer |
| VALUE (scroll) | Amp envelope sustain level | `EA-S` | ± full scale |
| FIL ATT / DEC / SUS / REL | Filter envelope stage | `EF-A` `EF-d` `EF-S` `EF-r` | as above |
| Shift + filter / amp ADSR knobs | Env 3 / Env 4 stage | `E3-A`… `E4-r` | as above |
| GLIDE | Glide time | `GLId` | 16× shorter or longer |
| VALUE / web | Unison detune * | `dEtU` | ±50 cents |
| VALUE / web | Master volume * (a 0-to-1 source counts down from full, as the VCA) | `VOL` | closed to full |
| VALUE / web | Macro 1, Macro 2 | `nAC1` `nAC2` | −1 to 1 |

\* Single wires for all voices: they follow the most recently played note of the layer. Everything else acts per
voice.

‡ Pitch depths follow a squared curve, fine near zero for vibrato: depth 5 = 6 cents, 10 = 25 cents, 20 = 1
semitone, 50 = 6 semitones, 99 = 24 semitones.

† The VCA destination is a gain on the amp envelope, so it never opens a voice that isn't playing. A swinging
source (an LFO, random, note) at depth 99 goes from closed to fully open: tremolo, or a gate with the step shape.
A 0-to-1 source (envelopes, velocity, mod wheel, aftertouch) turns the volume down where it's low: velocity → VCA at
99 is plain velocity sensitivity, aftertouch → VCA at 99 is silent until you press. `FIX` → VCA turns the layer down
by its depth, whichever the sign: 50 plays it at half, so it sets one layer's level against the other's in split or
stack mode (`FIX` → `VOL` does the same to the master volume, for both layers).

The LFO rate is per voice in `PoLY` retrigger mode. Otherwise it follows the most recently played note.

Rate modulation sweeps smoothly, also on a clock-synced LFO: depth 99 is ±4 octaves, so LFO 2 (triangle) → LFO 1
rate at depth 50, with LFO 1 synced at `1-8`, sweeps it between 1/2 and 1/32. While it's swept the LFO leaves the
beat; with retrigger `FrEE` it slides back onto the bar within a cycle or two once the modulation stops.

### Ideas to try

- **Delayed vibrato:** set LFO 1 to pitch with AMOUNT at 0. Route 7 (Env 3) → LFO AMOUNT at depth 30, and give
  Env 3 a slow attack.
- **Faster vibrato for harder notes:** use `PoLY` retrigger and route 4 (velocity) → LFO FREQ.
- **Analog-style drift:** LFO 2 on `drFt` at a slow rate with `PoLY` retrigger, routed to pitch at a small
  AMOUNT. Every voice wanders on its own.
- **Sample & hold filter:** LFO 2 on `S-H`, clock-synced at `1-16`, routed to the filter.
- **Split bass and lead:** split at C3. Give layer 1 a short filter envelope, and layer 2 glide plus Env 4 →
  pitch A for a scoop.
- **Pressure brightens:** route 6 (aftertouch) → CUTOFF.
- **Trance gate:** an LFO on `StEP`, clock-synced, routed to the VCA at 99 (see Step patterns above).

## Shift (Sync Source)

Hold **Sync Source**, or press it twice quickly to latch it (LED on; twice again, or hold and release, to leave).
While Shift is on, the knobs don't edit their printed parameters. A knob you turn nudges a secondary value by how
far you turn it, so nothing jumps and nothing needs picking up; the display shows the name, then the value.

| Shift + knob | Secondary value |
|---|---|
| Filter ATT / DEC / SUS / REL | Env 3 (with pickup, as a loaded patch's knobs) |
| Amp ATT / DEC / SUS / REL | Env 4 (the same) |
| LFO FREQ | The selected LFO's delay (`dLY`, 0-99: off, 20 ms to 5 s) |
| LFO AMOUNT | The selected LFO's fade-in (`FAdE`) |
| GLIDE | Drift (`drFt`, 0-99) |
| M TUNE | Unison detune (`dEtU`, 0-50 cents) |
| FINE | Pitch-bend range (`bEnd`, 1-12 semitones) |
| CUTOFF | Macro 1's offset (`nAC1`, -99 to 99): a hand-set input to the macro, saved in the patch |
| RESONANCE | Macro 2's offset (`nAC2`) |
| A LEVEL / B LEVEL | The CC A / CC B source numbers (`CC.A` / `CC.b`, 1-127; a system setting) |
| NOISE | The MIDI channel the synth listens on (`CHnL`: `ALL`, 1-16; a system setting, see Playing) |
| FIL ENV AMT | Knob pickup (`P.UP`: a quarter turn up `on`, down `OFF`; a system setting, see The panel at a glance) |

| Shift + button | Does |
|---|---|
| Rec | Compare: the stored patch sounds while Rec is held (`CoNP`), your edit comes back when you let go (`EdIt`) |
| Perf | Undo the last patch load or init (`Undo`): the sound comes back with its step sequence, morph target, name and slot. Again redoes |
| Preset | Save to the current slot (`donE`), without Rec, slot, Rec |
| Seq 1 / Seq 2 | Copy the edit layer onto that layer (`CoPY`) |
| 1-9 | Open that Settings page directly |
| 0 | Panic: every note off, the pedal cleared, the sequence stopped (`PAnC`) |

When Shift is released the knobs go into pickup, as after loading a patch, so letting go doesn't move anything
(with pickup off too: a knob takes over when you move it after).

## Clock

Synced LFOs, cycling envelopes and the sequence follow one clock, chosen with Shift + Sync Clock (`nIdI`, `Int`,
`SYnC`; also the web page's Voice section or `pro800 set clock`), a system setting that survives power-off:

- **MIDI** (the default): MIDI clock, Start, Continue and Song Position as they come, from USB or the 5-pin
  socket. Without a clock the synced things run at the internal tempo.
- **Internal**: the firmware's own clock, 30-300 BPM. **Tap Sync Clock** two or more times to set it (`tAP`, then
  the BPM; the last tap is a beat), or `pro800 set bpm`, or the web page. **Hold Sync Clock** half a second to
  restart the bar (`StAr`): the next tick is the downbeat, so synced LFOs line up. The tempo is remembered.
- **SYNC IN**: the rear jack's pulses set the tempo and the phase, two pulses per quarter note (Korg-style). Not
  yet tried with a real clock source.

The Sync Clock LED blinks on the beat of whichever clock is in use, so a missing MIDI clock shows at once.

## Settings menu

Settings opens it (LED on, `SEt`). A number key picks a page, VALUE changes the value, and Settings closes it.

| Key | Page | Values |
|---|---|---|
| 1 | `SYnC` / `SYn2`: selected LFO's sync | `FrEE`, `CLoC` |
| 2 | `trIG` / `trI2`: selected LFO's retrigger | `FrEE`, `1St`, `EACH`, `PoLY` |
| 3 | `LAYr`: layer mode (Arp Up-Dn: `Lr.Uo`, the voices per layer) | `OFF`, `SPLt`, `StAC`, `CHAn`, `8CH`, `nPE`; `AUto`, `1-7` … `7-1` |
| 4 | `SPLt`: split note | 0-127, or play a note |
| 5 | `trAC`: keyboard tracking (also the switch) | `OFF`, `HALF`, `FULL` |
| 6 | `En.A` / `En.F` / `En.3` / `En.4`: envelope mode (6 again: next one; Arp Up-Dn: its trigger `tr.A`, timing `tI.A`, curve `CU.A`, cycles `Cn.A`; ATT / DEC: delay / hold) | `AdSr`, `dAHd`, `CYCL`, `8bAr` … `1-32`; trigger `dn`, `UP`; timing `SEC`, `SYnC`; curve `AnLG`, `LIn`, `SUEL`; cycles `LOOP`, `1` … `99` |
| 7 | `bEnd`: pitch bend range | 1-12 semitones (default 2) |
| 8 | `dEtU`: unison detune, cents between the outermost voices | 0-50 (default 14) |
| 9 | `UOIC`: the edit layer's voice mode | `POLY`, `nono`, `LEG` |
| 0 | `drFt`: drift | 0-99 |

Settings live in the patch, so save the patch to keep them.

## Tuning

Tune measures every oscillator and filter and stores the tables in the EEPROM, where they survive firmware
updates. It takes about 25 seconds, silent, with `tUnE` on the display. A new firmware tunes by itself the first
time if it finds no tables, or tables made for a different way of writing the CVs (0.7.8 and 0.7.9 each did this once after their
update: its refresh holds slightly different voltages, and the old tables read up to 28 cents sharp with it). Retune
after the synth has warmed up for best results.

## The web page

Open https://klaine.github.io/pro-8000/editor/ in **Chrome or Edge**, or start it from the repository with
`scripts/web.sh` and open http://localhost:8800. It needs WebMIDI with SysEx, which Safari and the desktop app's built-in
browser don't offer. Click Connect; from then on the page connects by itself whenever it opens, on the tab you left. The
header links the two guides (How it works, Sound design), and the PRO·800 logo the site's home page, all in the same
browser tab: coming back reconnects and restores the routing.

The page works with any firmware from the 0.9.0 release on, and shows what the connected firmware has: with an
earlier build it hides what that build lacks (the Timing menu and the Cycles knob, and the Trigger menu while an
envelope cycles), the Device cheat sheet describes that build's panel, and the status line says what's missing.

| Tab | What it does |
|---|---|
| **Sound** | On top, the synth's own faceplate: oscillators, poly-mod, LFO-MOD (with a rotary for LFO 1's nine shapes), noise, glide, filter (its Env select switch is the keyboard-tracking switch, as on the synth: down, middle and up make the four ADSR knobs edit the filter envelope, Env 3 or Env 4; it follows the synth's switch, and a click holds until you move that), amplifier, Macro 1 (M TUNE) and volume, with knurled knobs and bat-lever switches laid out as on the synth. Values show under the pointer, or always with the Values switch. A red arc around a knob is its modulation range: how far the edit layer's mod routes can move it from where it stands (both ways for sources that swing either side of the middle, one way for 0-to-1 sources); hover for the routes. Each oscillator section shows its waveform (as measured at the output: the enabled waves added at their own levels and pulse width, the saw falling), the filter section its response curve (above its knobs) and the envelope its ADSR knobs are editing, and the amplifier its envelope; under each envelope picture are its Mode menu (ADSR / DAHDSR / cycle / synced cycle), Trigger menu (key down / key up), Timing menu (seconds / note values), Curve menu, Delay and Hold knobs (DAHDSR), Cycles knob (cycling) and ⇆ to copy it from the other layer (in the filter section they follow the Env select switch). Below it, "Beyond the panel": Env 3 and Env 4 (picture, Mode, knobs, ⇆), LFO 1 and LFO 2 (shape, rate, amount, delay and fade, sync, retrigger, routing, depth bars to VCA / cutoff / pitch A and B / PWM, the step grid for the Steps shape; LFO 1's rate, amount, shape and routing are also on the faceplate), voice settings (layers, filter keytracking, voice mode and note priority, drift, scale and root, morph, Mutate, bend, detune), the mod matrix (with a knob for each macro's offset: its ring is what the routes into the macro can add, the text beside it what feeds and what it drives) and the sequencer. Drag a knob up and down (shift: fine), scroll over it or use the arrow keys; double-click resets it. The red screen shows the last control you touched and the patch; click the patch's name there to rename it (Enter or clicking away saves it to the slot, Escape leaves it). Layer 1 / Layer 2 are in the header; knobs, depth bars and the mod matrix edit that layer. It follows the synth about once a second; moving a control puts that knob in pickup on the panel. Amber knobs are shared by both layers (mixer levels, noise, resonance, PW A / B, poly-mod osc B, volume). |
| **Device cheat sheet** | How to reach everything this firmware adds from the synth's own panel: the remapped buttons, the display, patches, layers, LFOs, envelopes, mod assign with its source keys and destinations, the settings menu, MIDI and recovery, and what's web-only. |
| **Patches** | Load, save the current sound to any slot, rename, clear, export one patch as a `.p8p` file and import it into any slot, back up all 128 as a `.bin`. **In one go:** format the patch memory, copy the whole factory bank into the empty slots, or install the preset bank (66 sounds into P1-P66, from the page's own copy, or a bank file such as the release's `pro8000-presets-<version>.json`; a bank pasted or opened in the JSON box installs too), each after a confirmation. Empty slots are hidden unless "show empty slots" is ticked. **Sound as JSON:** the current sound as JSON in the preset files' format (`presets/README.md`: knobs 0-99, buttons, settings, routes, LFO step patterns, the sequence, the morph target, by the `pro800` tool's names): show it in the text box, copy it, or download it as a `.json` file. Paste JSON into the box (or open a `.json` file, such as one of `presets/`) to load it into the sound: it starts from the init sound and sets what the JSON names, after checking every name and range, and settings the connected firmware hasn't got are left out with a note. Then save it into a slot with its name. A file from here installs with `presets/install.sh` too. Copy factory sounds into empty slots. |
| **Play** | An on-screen keyboard (also playable from the computer keyboard), and a MIDI router that forwards another MIDI input, such as the Ableton Move, to the synth. The browser remembers the router's input and its clock box, and routes again whenever that input is there (after a reload, or when it's plugged back in); choose (off) to forget it. |
| **System** | Firmware version, timing, autotune and the tuning tables, and knob pickup on or off (0.9.3). |
| **Panel mapping** | The developer tools used to map the panel. |

## The command-line tool

`scripts/tools.sh` builds `build/pro800`. `build/pro800 help` lists every command. The ones you'll use most:

```
build/pro800 settings
build/pro800 params 1
build/pro800 patches
build/pro800 patch rename A07 Warm Pad
build/pro800 factory import 12 B00
build/pro800 set shape 1 2 drift
build/pro800 route 1 env3 lfo-amount 30
build/pro800 install build/pro800-0.9.2.syx --yes
```

## Updating, recovery and going back to stock

- **First install:** after flashing from the stock firmware the slots are empty or unreadable; fill them from the
  web page (see Patches: "Filling the slots after installing").

- **Update:** `build/pro800 install build/pro800-<version>.syx --yes` puts the synth into update mode, flashes it
  and waits until it runs the file's version. Patches and tuning stay. If the stock firmware doesn't go into update
  mode, it stops without flashing: hold 0 and 7 at power-up, then run it again.
- **If a firmware won't start:** hold buttons **0 and 7** while switching on to reach the bootloader. Then flash
  any firmware `.syx` with `build/pro800 flash` or Behringer's SynthTribe. The bootloader can't be overwritten
  from here.

### Going back to the stock firmware

Do these steps in order. **Fix the patch memory (step 2) before you flash stock (step 3):** the stock firmware can't
read PRO-8000's patches, and the step 2 tools only work while PRO-8000 is running.

**What has been tested:** one synth went back to stock 1.4.6 by route A and by route B in step 2, each time with
route 2 in step 3. The SynthTribe route (route 1) has failed every time on that synth, so read its note first.

**1. Save your PRO-8000 sounds (optional).** Open the [web editor](https://klaine.github.io/pro-8000/editor/) in Chrome
or Edge, go to the Patches tab and click **Back up everything**. Only PRO-8000 can read this file, so it is useful only
if you come back to PRO-8000 later.

**2. Fix the patch memory.** Choose one route. Leave the synth switched on, running PRO-8000.

- **Route A: you saved a full EEPROM image of your synth before you first installed PRO-8000** (with `pro800 eeprom`).
  This puts back your stock patches and settings exactly. It needs a Mac and the command-line tool: clone this
  repository, run `scripts/tools.sh`, then run:

  ```bash
  build/pro800 eeprom-write <your-image.bin> --yes
  ```

  Wait for the line `verified: the EEPROM matches`. The message then says to power-cycle the synth: don't, go straight
  to step 3. Use only an image from your own synth, because it holds that synth's data as well as its patches.

- **Route B: you have no image.** Your original stock patches can't be brought back from the synth, because PRO-8000
  overwrote them. In the web editor's Patches tab, click **Format the patch memory**. Every slot becomes empty, which
  is also how the stock firmware stores an empty slot. The 100 factory sounds stay in the EEPROM, and step 4 brings
  them back. If you kept `.syx` patch backups from before installing, you can load them after step 4.

**3. Flash the stock firmware.** First quit Chrome, Edge, and any other program that uses MIDI, completely (Cmd+Q on a
Mac). An open editor page can make the update fail, because the bootloader's USB port is also called "PRO 800". Connect
the synth by USB. Switch it off, hold buttons **0** and **7**, and switch it on. The display shows `boot` and stays there, which means
the bootloader is ready, so let go of the buttons. Then choose a route.

- **Route 1: SynthTribe.** Open Behringer's SynthTribe, go to **Update**, and start the update. It downloads the
  current stock firmware itself. It may show the PRO-8000 version number or `0.0.0` as the current firmware; that's
  normal. Clicking update changes the display to `UPDA`.

  *This has never finished on our synth* (SynthTribe 3.2.4, macOS 26, Chrome closed): the bar stays at 0% or 1%
  and the display stays on `UPDA`. PRO-8000 isn't running at that point, so the cause isn't in its code, and SynthTribe
  may behave differently on another computer. If the bar hasn't moved after 2 minutes, quit SynthTribe, switch the
  synth off and on with 0 and 7 held again, and use route 2. The stalled attempt does no harm.

- **Route 2: the command-line tool (Mac).** Run SynthTribe's update once so that it downloads the firmware, then quit
  it, even if the bar is stuck. The file is in `~/Library/Caches/com.behringer.synthtribe/fsCachedData/`. It has a random name and is about
  169 kB. Copy it somewhere and name it `pro800-stock.syx`, then run:

  ```bash
  build/pro800 flash pro800-stock.syx --yes
  ```

  It takes about a minute. A good update prints a reply ending in `03 31 F7` near the start and one ending in
  `01 00 00 F7` at the very end. Blocks answered with `01 00 07` mean something else was talking to the synth: quit
  it, switch the synth off and on with 0 and 7 held, and flash again.

**4. Check the result.** Switch the synth off and on if it hasn't restarted by itself. It should start as a stock
PRO-800, showing a patch number such as `A-13`. SynthTribe, or `build/pro800 version`, should show the stock version
(`firmware 1.4.6` when this was written); if the tool gets no reply, wait a few seconds and try again. The bootloader
also answers version requests, so a number alone doesn't prove the flash worked: the synth must also start and play.

After route B, the synth is out of tune and its slots are empty, so a key plays a plain saw with the filter open.
Finish it like this:

1. Press **Tune** and let it finish. It takes longer than PRO-8000's Tune, going voice by voice.
2. Press **Settings**, then **0**, then **0** again to confirm the factory reset. Don't switch off during it.
3. The 100 factory sounds are back in the first 100 slots, starting with "Organ I". The other 300 slots stay empty.
4. The reset also returns the system settings to their defaults. The MIDI channel goes back to `DIPS`, so set it again
   if your MIDI source plays on one channel.

**If it goes wrong:** the bootloader can't be overwritten. If the tool says `MIDI port "PRO 800" not found`, wait a few
seconds and run it again. If the display stays on `boot` after a flash or shows `noFW`, switch the synth off and on,
then go back to step 3. To return to PRO-8000 at any point, enter the bootloader the same way and
flash a PRO-8000 `.syx` from the [releases](https://github.com/klaine/pro-8000/releases).

## Known limitations

- Waveform levels within one oscillator (e.g. saw louder than triangle on oscillator A) can't be mixed: the
  waveform buttons are on/off lines to the voice board. Put the second waveform on the other oscillator instead.
- Oscillator B with all its waveforms off is silent, except while poly-mod from it is in use (PMOD OSC B up with
  FREQ A or FILTER on): then a thin pulse of it can still be heard. Its square has no on/off line, so it's switched
  off by pushing the pulse width to an end, and the silent end would detune oscillator A and the filter through
  poly-mod.
- MIDI OUT / THRU isn't implemented, and SysEx works over USB only. The sequencer, scale, morph and note priority
  are set on the web page only.
- 128 patches, each with its sequence; the 0.7.x patches are not carried over (the EEPROM backups keep them).
- The LFO routing buttons always show LFO 1's routing. LFO 2's shows on the display when toggled, and on the web
  page.
- Going to manual mode (Preset) makes every button live, so LFO 1's routing jumps to what the buttons show.
