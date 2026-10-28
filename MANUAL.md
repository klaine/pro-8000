# PRO-800 custom firmware: user manual

For firmware 0.7.6. This firmware replaces the stock PRO-800 firmware. Same front panel, different engine:

- two layers (split, stack or by MIDI channel);
- two LFOs per layer with eight shapes (a programmable step pattern among them), clock sync and per-voice
  retrigger;
- four envelopes per voice;
- an 8-route mod matrix per layer;
- a step sequence per patch, played from a key and transposed by it, with up to four automation lanes;
- autotune that keeps every oscillator within about a cent;
- a browser editor.

The stock sequencer and arpeggiator are gone; their buttons do other jobs now.

## The panel at a glance

| Control | What it does now |
|---|---|
| Knobs | As printed, with a few changes. LFO FREQ / LFO AMOUNT edit LFO 1 or LFO 2. The filter ADSR knobs can edit Env 3 / Env 4. |
| FREQ A / FREQ B | ±36 semitones (6 octaves in all) in semitone steps; the display shows the interval, e.g. `-12`, ` 7`. |
| FINE | Oscillator B, ±100 cents, centre = in tune; the display shows cents. |
| Keyboard-tracking switch | Picks what the filter ADSR knobs edit: bottom = filter envelope, middle = Env 3, top = Env 4. Keyboard tracking itself is a setting. |
| LFO Shape button | Steps to the next shape at every press: `trI`, `PULS`, `SA.UP`, `SA.dn`, `SInE`, `S-H`, `drFt`, `StEP`. |
| VALUE knob | Changes the setting on the display: patch number, menu value, mod depth, or the shape just after pressing LFO Shape. |
| Seq 1 / Seq 2 | Choose the layer the knobs edit (LED). |
| Arp Up-Dn | Choose LFO 1 or LFO 2 for the LFO knobs, the shape button and the LFO routing buttons (LED lit = LFO 2). |
| Arp Assign | MOD ASSIGN: the mod matrix (LED blinks while assigning). |
| Preset | Preset mode (a patch loaded) ↔ manual mode (the panel is live). |
| Rec | Save (blinks, choose the slot, Rec again). |
| Perf | Load the init sound: one sawtooth, filter open. |
| Settings | Opens the settings menu (keys 1-5). |
| Tune | Autotune (about 25 s, silent). |

The display shows a knob's value while you turn it, in real units where it has one:

| Knob | Shows | Range and curve |
|---|---|---|
| FREQ A / B | semitones (`-12`, `  7`) | ±36, whole semitones |
| FINE, M TUNE | cents | ±100 |
| CUTOFF | Hz; with a point from 1 kHz on, kHz (` 737`, ` 1.24`, ` 16.7`) | 16 Hz-16.7 kHz, exponential |
| FIL ENV AMT | semitones, signed (`-4.5`, `  18`) | ±72, squared: fine near the centre (25 % from the centre = 4.5, half = 18) |
| PMOD FIL ENV | semitones (` 3.00`, ` 12.0`) | 0-48, squared (a quarter = 3, half = 12) |
| LFO FREQ | Hz (` 1.60`), or the clock division when synced | 0.05-51 Hz, exponential |
| GLIDE | time constant (` 150` ms, ` 1.25` s), `OFF` at the bottom | 2 ms-2 s |
| Envelope times | ms / s (see Envelopes) | |
| everything else | 0-99 | |

A digit display can't show a plus sign, so positive values have none. A dot after the value means the knob hasn't
**picked up** the stored value yet. Turn it past the stored value (or onto it) and it takes over. This happens:

- after loading a patch;
- after switching layer, LFO or envelope;
- after editing from the web page.

## Playing

Play over USB MIDI or the 5-pin MIDI IN socket, on any channel. Pitch bend is ±2 semitones by default (Settings key 7, per patch). The mod wheel adds LFO 1 depth and is also a mod
source. Aftertouch is a mod source too, and it's per voice: poly pressure (the Move's pads send it) moves only the
voice playing that note; channel pressure moves them all. The firmware follows MIDI clock (Start, Stop and Clock)
for LFO sync.

The 5-pin MIDI IN socket (from 0.7.6) takes the same notes, controllers, pressure, bend, program changes and clock,
so a keyboard with a MIDI out plays the synth without a computer. SysEx (the web page, the tool, updates) needs USB.
The USB socket is a device port, so a USB-only keyboard needs a computer or a USB MIDI host box in between (or the
web page's MIDI router).

- **Sustain pedal** (CC 64): keys let go while it's down keep sounding (and keep a sequence running) until it's
  released.
- **Program change** picks a patch in the bank chosen with bank select (CC 0 or 32: 0 = A, 1 = b); programs 100-127
  of bank A reach b.00-b.27.
- **Controllers** set knobs on the layer the channel plays (layer 2 for channel 2 in channel mode), else the layer
  you're editing; the knob then picks up as after loading a patch:

| CC | Knob | CC | Knob | CC | Knob |
|---|---|---|---|---|---|
| 5 | Glide | 21 | Noise | 29 | PMOD filter env |
| 14 | FREQ A | 22 | Filter env amount | 30 | PMOD osc B |
| 15 | FREQ B | 23-26 | Filter A / D / S / R | 31 | Master tune |
| 16 | FINE | 27 / 28 | LFO 2 rate / amount | 70 / 72 / 73 / 75 | Amp S / R / A / D |
| 17 / 18 | PW A / B | 71 | Resonance | 76 / 77 | LFO 1 rate / amount |
| 19 / 20 | Level A / B | 74 | Cutoff | 85-88, 89-90 + 102-103 | Env 3, Env 4 A / D / S / R |

### Voice modes, drift, scale, morph

- **Voice mode** (per layer; Settings key 9 `UOIC`, or the web page): `POLY`, mono (`nono`: one voice, every new
  note retriggers the envelopes) or legato (`LEG`: one voice, retriggers only after all keys are up). Note priority
  (web page): last, low or high note; it also applies to unison, which plays the same line on all the layer's
  voices.
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

There are 200 patches in two banks, shown as ` A.07` and ` b.12`. Each has its own step sequence (from 0.7.0; the
stock firmware's banks C and D hold the sequences).

- **Load:** in preset mode (Preset LED on), type two digits to pick a patch in the current bank, or turn VALUE to
  step through all 200. Boot loads the last patch.
- **Edit:** a loaded patch's knobs are in pickup and its buttons keep their stored state until you press them.
  Press Preset for manual mode, where the whole panel is live as it is.
- **Save:** press Rec (it blinks), choose a slot with two digits or VALUE, then press Rec again (`donE`). Preset
  cancels. The patch's name follows the sound into an empty slot.
- **Init:** Perf loads a plain sound. Rec saves it anywhere.
- **Names, export, backup, the factory bank:** on the web page (Patches tab).

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

Settings → key 3 (`LAYr`) has four modes:

| Mode | Voices | Notes |
|---|---|---|
| `OFF` | Layer 1 on all 8 voices | — |
| `SPLt` | 4 each | Layer 1 below the split note, layer 2 from it up. Set the note with Settings → key 4, VALUE, or by playing it while that page shows. |
| `StAC` | 4 each | Both layers on every note. |
| `CHAn` | 4 each | MIDI channel 2 plays layer 2, every other channel layer 1. |

Seq 1 / Seq 2 choose the layer the knobs edit (`L  1` / `L  2`).

**Per layer:**
- FREQ A/B and FINE;
- CUTOFF and FIL ENV AMT;
- all four ADSRs;
- GLIDE and M TUNE (layer fine-tune);
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
`1-8t`, `16d`, `1-16`, `16t`, `1-32`. MIDI Start lines the LFO up with the bar.

**Retrigger** (Settings → key 2, `trIG` / `trI2`):

| Display | Mode |
|---|---|
| `FrEE` | Free-running; with the clock, the phase follows the grid |
| `1St` | Restarts on the first key after all were released |
| `EACH` | Restarts on every key |
| `PoLY` | Every voice runs its own LFO from its note-on: per-voice LFOs |

Sync and retrigger are set per LFO (LFO 1, LFO 2) and apply to both layers.

## Envelopes

Every voice has four ADSRs: filter, amp, Env 3 and Env 4. The keyboard-tracking switch picks which envelope the
four filter ADSR knobs edit (`FEnV`, `EnV3` or `EnV4` on the display, with pickup). Env 3 and Env 4 do nothing
until a mod route uses them. Keyboard tracking (`OFF` / `HALF` / `FULL`) is Settings → key 5 (`trAC`).

While you turn a time knob the display shows the time: milliseconds as a whole number (` 150`), seconds with a
point (` 1.25`, ` 12.5`).

| Knob position | 0 | 10 % | 25 % | 50 % | 75 % | 100 % |
|---|---|---|---|---|---|---|
| Attack | instant | 3 ms | 17 ms | 150 ms | 1.25 s | 10 s |
| Decay, release | 3 ms | 9 ms | 37 ms | 310 ms | 2.5 s | 20 s |

Decay and release times are how long the fall takes to reach −60 dB, so a release showing 2 s is gone at 2 s.
The attack has the concave shape of an analog envelope: it shoots up first and eases into the top. Decay and
release fall exponentially. Sustain is a level, 0-99. The amp envelope reaches the VCAs about every 0.9 ms and the
filter envelope the cutoff about every 1.7 ms (with all 8 voices sounding; faster with fewer), so attacks below
about 1 ms all sound like an instant attack. Everything else (pitch, LFOs, routes) updates about every 3.4 ms, or
faster with fewer voices sounding.

### Cycling envelopes

Any envelope can cycle while the key is held, like a one-shot LFO per note that restarts itself. Turn it on in
Settings → key 6, which shows `CY.A` (amp), `CY.F` (filter), `CY.3` or `CY.4`. It opens on the envelope the filter
ADSR knobs edit, or amp; press 6 again for the next one. VALUE chooses `OFF`, `FrEE` (cycling on its own times) or
a clock division (`8bAr` … `1-32`, synced cycling, below). This is per layer and stored in the patch. On the web
page, each envelope has a Mode menu with the same choices.

While it cycles, the knobs mean:

| Knob | In a cycle |
|---|---|
| ATT | Attack: the rise to the top (0-10 s) |
| SUS | Hold: time at the top (0-10 s) |
| DEC | Fall: the drop to zero (3 ms-20 s) |
| REL | Rest: time at zero before the next attack (0-10 s) |

On key up the envelope falls to zero with the DEC time. One cycle lasts attack + hold + fall + rest. Some ideas:

- A cycling amp envelope with short attack and fall makes rhythmic gating that restarts with every note.
- A cycling Env 3 → CUTOFF gives each note its own filter "wah" pattern.
- Cycling Env 4 → pitch A with a long rest gives a recurring blip.

### Synced cycling

With a clock division instead of `FrEE`, one cycle lasts that division at the MIDI clock's tempo (120 BPM when no
clock has arrived). The four knobs become weights that share the cycle out: attack, hold (SUS), fall (DEC) and rest
(REL) in proportion. Turning one up takes time from the others, and REL at 0 means no gap. While you turn one, the
display shows its share of the cycle in percent; the web page shows all four.

The cycle restarts with each key and follows tempo changes, so it stays on the beat however long you hold. A
synced amp envelope at `1-16`, with a short attack, some hold and a longer fall, is a pulsing gate with shaped
edges. On key up it falls with its share of the cycle.

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
  plays a sequenced bass and the other plays normally.
- **Automation lanes:** up to four, one value per step (−99..+99), optionally gliding from step to step. They're
  mod sources Seq A-D: choose a destination next to each lane (a route at depth 99 on the layer you're editing) or
  route them in the Mod matrix with any depth. Steps and lanes share the space in the sequence's slot: 60 steps
  with no lanes, 48 with 1, 40 with 2, 34 with 3, 30 with 4.
- **On the web page also:** Euclid fills the steps with k hits spread evenly over the length; Record writes notes
  you play (the on-screen keys or the routed MIDI input, e.g. the Move) into the steps one by one, relative to the
  first; Reset steps.
- **Saving:** the sequence is saved with the patch (REC, or Save in Patches) and loaded with it. PERF's init sound
  has none.

## Mod matrix (MOD ASSIGN)

Each layer has 8 routes. A route adds source × depth (−99 to 99) to a destination.

1. Press **Arp Assign** (LED blinks, display `SrC`). The knobs go into pickup, so touching one won't change the
   sound.
2. Press a **number key** for the source.
3. **Touch the destination:** turn its knob a little, or press its LFO routing button.
4. Turn **VALUE** to set the depth for the edit layer. 0 removes the route; `FULL` means all 8 are in use.
5. Press **Arp Assign** again to finish.

Touching a destination shows its current depth. Seq 1 / Seq 2 and Arp Up-Dn still work while assigning.

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
| (web only) | Sequence lanes A-D | |

The Morph destination (web page only) fades toward the morph target patch (see Voice modes, drift, scale, morph).

| Touch | Destination | Display | Full depth |
|---|---|---|---|
| FREQ A | Pitch A | `P.A` | ±24 semitones ‡ |
| FREQ B or FINE | Pitch B | `P.b` | ±24 semitones ‡ |
| M TUNE | Pitch A and B | `PIt` | ±24 semitones ‡ |
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

\* Single wires for all voices: they follow the most recently played note of the layer. Everything else acts per
voice.

‡ Pitch depths follow a squared curve, fine near zero for vibrato: depth 5 = 6 cents, 10 = 25 cents, 20 = 1
semitone, 50 = 6 semitones, 99 = 24 semitones.

† The VCA destination is a gain on the amp envelope, so it never opens a voice that isn't playing. A swinging
source (an LFO, random, note) at depth 99 goes from closed to fully open: tremolo, or a gate with the step shape.
A 0-to-1 source (envelopes, velocity, mod wheel, aftertouch) turns the volume down where it's low: velocity → VCA at
99 is plain velocity sensitivity, aftertouch → VCA at 99 is silent until you press.

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

## Settings menu

Settings opens it (LED on, `SEt`). A number key picks a page, VALUE changes the value, and Settings closes it.

| Key | Page | Values |
|---|---|---|
| 1 | `SYnC` / `SYn2`: selected LFO's sync | `FrEE`, `CLoC` |
| 2 | `trIG` / `trI2`: selected LFO's retrigger | `FrEE`, `1St`, `EACH`, `PoLY` |
| 3 | `LAYr`: layer mode | `OFF`, `SPLt`, `StAC`, `CHAn` |
| 4 | `SPLt`: split note | 0-127, or play a note |
| 5 | `trAC`: keyboard tracking | `OFF`, `HALF`, `FULL` |
| 6 | `CY.A` / `CY.F` / `CY.3` / `CY.4`: cycling envelope (6 again: next one) | `OFF`, `FrEE`, `8bAr` … `1-32` |
| 7 | `bEnd`: pitch bend range | 1-12 semitones (default 2) |
| 8 | `dEtU`: unison detune, cents between the outermost voices | 0-50 (default 14) |
| 9 | `UOIC`: the edit layer's voice mode | `POLY`, `nono`, `LEG` |
| 0 | `drFt`: drift | 0-99 |

Settings live in the patch, so save the patch to keep them.

## Tuning

Tune measures every oscillator and filter and stores the tables in the EEPROM, where they survive firmware
updates. It takes about 25 seconds, silent, with `tUnE` on the display. A new firmware tunes by itself the first
time if it finds no tables. Retune after the synth has warmed up for best results.

## The web page

Start it from the repository with `scripts/web.sh`, then open http://localhost:8800 in **Chrome or Edge**. It needs
WebMIDI with SysEx, which Safari and the desktop app's built-in browser don't offer. Click Connect.

| Tab | What it does |
|---|---|
| **Sound** | On top, the synth's own faceplate: oscillators, poly-mod, LFO-MOD (with a rotary for LFO 1's eight shapes), noise, glide, filter (its Env select switch is the keyboard-tracking switch, as on the synth: down, middle and up make the four ADSR knobs edit the filter envelope, Env 3 or Env 4; it follows the synth's switch, and a click holds until you move that), amplifier, master tune and volume, with knurled knobs and bat-lever switches laid out as on the synth. Values show under the pointer, or always with the Values switch. Below it, "Beyond the panel": a scope (both oscillators' waveforms, the filter curve), the four envelopes (a picture of each, a Mode menu for ADSR / cycle / synced cycle, Env 3 / 4's knobs, ⇆ to copy from the other layer), LFO 1's extras and LFO 2 (shape, rate, amount, sync, retrigger, routing, depth bars to VCA / cutoff / pitch A and B / PWM, the step grid for the Steps shape), voice settings (layers, filter keytracking, voice mode and note priority, drift, scale and root, morph, Mutate, bend, detune), the mod matrix and the sequencer. Drag a knob up and down (shift: fine), scroll over it or use the arrow keys; double-click resets it. The red screen shows the last control you touched and the patch. Layer 1 / Layer 2 are in the header; knobs, depth bars and the mod matrix edit that layer. It follows the synth about once a second; moving a control puts that knob in pickup on the panel. Amber knobs are shared by both layers (mixer levels, noise, resonance, PW A / B, poly-mod osc B, volume). |
| **Device cheat sheet** | How to reach everything this firmware adds from the synth's own panel: the remapped buttons, the display, patches, layers, LFOs, envelopes, mod assign with its source keys and destinations, the settings menu, MIDI and recovery, and what's web-only. |
| **Patches** | Load, save the current sound to any slot, rename, clear, export one patch as a `.p8p` file and import it into any slot, back up all 400 as a `.bin`. Copy factory sounds into empty slots. |
| **Play** | An on-screen keyboard (also playable from the computer keyboard), and a MIDI router that forwards another MIDI input, such as the Ableton Move, to the synth. |
| **System** | Firmware version, timing, autotune and the tuning tables. |
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
build/pro800 install build/pro800-0.7.6.syx --yes
```

## Updating, recovery and going back to stock

- **Update:** `build/pro800 install build/pro800-<version>.syx --yes` puts the synth into update mode, flashes it
  and waits for the new firmware. Patches and tuning stay.
- **If a firmware won't start:** hold buttons **0 and 7** while switching on to reach the bootloader. Then flash
  any firmware `.syx` with `build/pro800 flash` or Behringer's SynthTribe. The bootloader can't be overwritten
  from here.
- **Back to stock:** the stock firmware can't read this firmware's patch format.
  1. Export or back up anything you want to keep (Patches tab, "Back up all 400").
  2. While this firmware still runs, write back the stock EEPROM image with
     `build/pro800 eeprom-write backup/2026-10-27/eeprom-stock.bin --yes`.
  3. Flash the stock firmware with SynthTribe.

## Known limitations

- Waveform levels within one oscillator (e.g. saw louder than triangle on oscillator A) can't be mixed: the
  waveform buttons are on/off lines to the voice board. Put the second waveform on the other oscillator instead.
- MIDI OUT / THRU isn't implemented, and SysEx works over USB only. The sequencer, scale, morph and note priority
  are set on the web page only.
- 200 patches (A and b): banks C and D hold the sequences.
- The LFO routing buttons always show LFO 1's routing. LFO 2's shows on the display when toggled, and on the web
  page.
- Going to manual mode (Preset) makes every button live, so LFO 1's routing jumps to what the buttons show.
