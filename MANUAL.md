# PRO-800 custom firmware: user manual

For firmware 0.6.3. This firmware replaces the stock PRO-800 firmware. Same front panel, different engine:

- two layers (split, stack or by MIDI channel);
- two LFOs per layer with eight shapes (a programmable step pattern among them), clock sync and per-voice
  retrigger;
- four envelopes per voice;
- an 8-route mod matrix per layer;
- autotune that keeps every oscillator within about a cent;
- a browser editor.

The internal sequencer and arpeggiator are gone; their buttons do other jobs now.

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

The display shows a knob's value (0-99, or semitones / cents for FREQ and FINE) while you turn it. A dot after the value means the knob hasn't
**picked up** the stored value yet. Turn it past the stored value (or onto it) and it takes over. This happens:

- after loading a patch;
- after switching layer, LFO or envelope;
- after editing from the web page.

## Playing

Play over USB MIDI, on any channel. Pitch bend is ±2 semitones. The mod wheel adds LFO 1 depth and is also a mod
source. Channel pressure and poly pressure act as aftertouch, also a mod source. The firmware follows MIDI clock
(Start, Stop and Clock) for LFO sync. The 5-pin MIDI input isn't supported yet: use USB, or route through the web
page's MIDI router.

## Patches

There are 400 slots in four banks, shown as ` A.07`, ` b.12`, ` C.99` and so on.

- **Load:** in preset mode (Preset LED on), type two digits to pick a patch in the current bank, or turn VALUE to
  step through all 400. Boot loads the last patch.
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
- the name.

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
release fall exponentially. Sustain is a level, 0-99. The engine updates every 2.3 ms, so attacks below about
3 ms all sound like an instant attack.

### Cycling envelopes

Any envelope can cycle while the key is held, like a one-shot LFO per note that restarts itself. Turn it on in
Settings → key 6, which shows `CY.A` (amp), `CY.F` (filter), `CY.3` or `CY.4`. It opens on the envelope the filter
ADSR knobs edit, or amp; press 6 again for the next one. VALUE sets `OFF` / `on`. This is per layer and stored in
the patch. On the web page, each envelope has a "cycle while held" box.

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

| Touch | Destination | Display | Full depth |
|---|---|---|---|
| FREQ A | Pitch A | `P.A` | ±24 semitones |
| FREQ B or FINE | Pitch B | `P.b` | ±24 semitones |
| M TUNE | Pitch A and B | `PIt` | ±24 semitones |
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

† The VCA destination is a gain on the amp envelope, so it never opens a voice that isn't playing. A swinging
source (an LFO, random, note) at depth 99 goes from closed to fully open: tremolo, or a gate with the step shape.
A 0-to-1 source (envelopes, velocity, mod wheel, aftertouch) turns the volume down where it's low: velocity → VCA at
99 is plain velocity sensitivity, aftertouch → VCA at 99 is silent until you press.

The LFO rate is per voice in `PoLY` retrigger mode. Otherwise it follows the most recently played note.

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
| 6 | `CY.A` / `CY.F` / `CY.3` / `CY.4`: cycling envelope (6 again: next one) | `OFF`, `on` |

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
| **Sound** | Every parameter of the layer you're editing: knobs, Env 3/4, LFO 2. It also has the LFO shapes, sync, retrigger and LFO 2 routing, and the layer mode, split and keyboard tracking. It follows the panel about once a second. Moving a slider puts that knob in pickup on the panel. |
| **Mod matrix** | Both layers' routes: change the source, destination or depth, remove routes, add new ones. |
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
build/pro800 install build/pro800-0.6.3.syx --yes
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
- 5-pin MIDI in and out, the sequencer and the arpeggiator aren't implemented.
- The LFO routing buttons always show LFO 1's routing. LFO 2's shows on the display when toggled, and on the web
  page.
- Going to manual mode (Preset) makes every button live, so LFO 1's routing jumps to what the buttons show.
