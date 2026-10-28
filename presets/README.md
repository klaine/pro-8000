# Preset bank: 64 sounds for the custom firmware

Sixty-four patches for firmware 0.8.1 and later, in slots P1-P64 (program change 0-63): 8 basses, 8 leads, 8 polys,
8 pads, 8 plucks, 8 arps and sequences, 8 effects and 8 curiosities that show the engine off. They're built for the
real PRO-800: two oscillators, the 4-pole filter, no effects, and every knob value comes from the firmware's own
curves (`MANUAL.md`: semitone steps on FREQ A / B, the squared filter-envelope and LFO curves, the exponential cutoff,
the 15 clock divisions), so `fil-env-amt 78` is +24 semitones and `cutoff 39` is 250 Hz on the display.

Nothing here touches the master volume. The mod wheel adds LFO 1 depth on every patch, so any patch with the
LFO → Freq button on (most leads) has wheel vibrato on top of what the notes say. Clock-synced patches run at 120 BPM
until a MIDI clock arrives.

## Install

```sh
scripts/tools.sh                     # build/pro800, once
presets/install.sh --dry-run         # prints every command, no synth needed
presets/install.sh                   # with the synth on USB
presets/install.sh --only 41-48,62   # a few slots
```

The installer runs one `build/pro800` command per step and prints it: `patch init`, the 13 latching buttons, every
knob, every setting, the mod routes, then `patch save P<n>` and `patch rename`. Each patch starts from the init
sound, so a rerun gives the same result, and the order (P25 before P59) means the morph target exists before the patch
that points at it. Options: `--tool <path>`, `--port <name>`, `PYTHON=`. About 5,600 commands in all; each one opens
MIDI and waits for the synth's acknowledgement, so the whole bank takes a while (`--only` for a group at a time).

The sounds then load with a program change, with the number keys in preset mode, or from the web page's Patches
tab (which also shows the names).

## The files

One JSON file per patch, `<slot>-<name>.json`:

| Key | Contents |
|---|---|
| `slot`, `name`, `group` | Slot 1-64, a name of up to 16 characters, one of bass / lead / poly / pad / pluck / arp / fx / other |
| `notes.sound`, `notes.shows` | What it sounds like, and which engine features it demonstrates |
| `buttons` | The latching buttons that are on (`a-saw` … `sync`, the tool's `set button` names); every other one is off |
| `params.shared` | The pots the layers share (mixer levels, noise, resonance, PW A / B, poly-mod osc B), 0-99 |
| `params.1`, `params.2` | Each layer's knobs, Env 3 / 4 and LFO 2, 0-99, by the tool's `param` names; layer 2 only when the patch uses it |
| `settings` | `set` commands as `"<setting> [args]": value`, e.g. `"shape 1 2": "sh"`, `"cycle-sync 1 env3": "1/4"`, `"voice 1": "legato last"`; every setting is listed, defaults included |
| `routes.1`, `routes.2` | Mod routes as `[source, destination, depth]`, the tool's `route` names, up to 16 per layer |
| `sequence` | The step sequence (arp group and P48): `on`, `step` (a clock division), `clock`, `bpm`, `swing`, `once`, `keys` (both / 1 / 2), `arp`, `lanes`, `smooth` (lanes that glide, e.g. `"A"`) and `steps`, each `{note, gate, vel, prob, ratchets, a-d}` with defaults left out (`note` 0, `gate` 50, the key's velocity, always, one hit); `{"rest": true}` is a rest, `gate` 100 a tie |
| `morph` | The morph target's slot (P59 only) |

`lib/plan.py` turns the files into command lines and checks every name and range against the tool's tables
(`tools/pro800.swift`), so a typo fails before anything reaches the synth. It's what `install.sh` runs; `plan.py
presets/` prints the plan on its own.

## The patches

**Basses (P1-P8)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P1 | Deep Sub | Triangle plus a hollow square two octaves down, legato glide. Legato mode, half keytracking, velocity to cutoff. |
| P2 | Acid Line | Saw and narrow pulse, resonance 72, a 36-semitone snap, mono retrigger. Accents (velocity → filter env amount), aftertouch → cutoff, wheel → resonance. |
| P3 | Reese Growl | Two saws 13 cents apart, the drift LFO nudging B per voice. Drift, the drift shape with per-voice retrigger. |
| P4 | Sync Punch | A synced to B, Env 3 dropping A 9 semitones in 250 ms. Env 3 → Pitch A, velocity → Env 3 decay. |
| P5 | Fat Stab | Saws an octave apart, a medium envelope: the workhorse. Velocity → cutoff and VCA. |
| P6 | Clock Wobble | Triangle LFO on the filter in quarter notes from the clock; the wheel takes it to 16ths. Clock sync, retrigger on first key, wheel → LFO rate. |
| P7 | PWM Bass | A 30 % pulse under slow PWM over a saw an octave down. LFO → PW, legato glide. |
| P8 | PolyMod Bass | Filter env kicks A's pitch, Env 4 lets B FM A for a 150 ms growl. Poly-mod, Env 4 → poly-mod osc B amount. |

**Leads (P9-P16)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P9 | Sync Lead | The Prophet sync lead: filter env sweeps A 24 semitones through poly-mod, legato glide. Aftertouch → LFO depth, bend → cutoff. |
| P10 | Delayed Vibrato | Saws 8 cents apart, bright; vibrato waits 250 ms and fades in over 430 ms per note. LFO delay and fade. |
| P11 | Press to Scream | Saw and pulse, resonance 65; pressure opens, pushes resonance and adds vibrato. Aftertouch to three destinations. |
| P12 | Hollow Square | Two pulses under PWM; press for a clock-synced octave trill from LFO 2's square. LFO 2 square synced to 1/8, depth under aftertouch. |
| P13 | Scoop Whistle | Triangles an octave apart, each note scooping up a semitone in 90 ms. Env 4 → pitch, velocity → Env 4 decay. |
| P14 | Velo Glide | Saws, legato with a 120 ms glide that shortens with velocity. Velocity → glide time. |
| P15 | Brass Solo | 60 ms filter attack into a 30-semitone sweep, late vibrato. Velocity → filter attack, CC 2 (CC B) → cutoff. |
| P16 | Human Lead | Saw and pulse; every note lands on a slightly different cutoff and pitch. Random → cutoff and pitch. |

**Polys (P17-P24)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P17 | Vintage Poly | Saw and PWM pulse, drift, every voice's B a few cents elsewhere. The Voice source → Pitch B. |
| P18 | Poly Brass | Saws 8 cents apart, a 40 ms attack into a 30-semitone sweep. Velocity and aftertouch on the filter. |
| P19 | Combo Organ | Squares an octave apart, Env 3 click, LFO 2 tremolo, vibrato that fades in. Env 3 as a click, LFO 2 → VCA. |
| P20 | Poly Sync | Synced saws with a 10-semitone poly-mod sweep, deeper on hard notes. Velocity → poly-mod env amount. |
| P21 | E Piano | Triangles, B at +19 FM-ing A while Env 4 decays: tine over body. FM through poly-mod shaped by Env 4 and velocity. |
| P22 | Poly Press | Saws, closed filter; pressing a key opens that voice and adds its own vibrato. Poly aftertouch, per-voice LFOs. |
| P23 | Old Poly | Heavy drift and a per-voice drift LFO on pitch and cutoff. Drift at 70. |
| P24 | Cycling Wah | Env 3 cycles in quarter notes from the clock: each note its own wah. A synced cycling envelope → cutoff. |

**Pads (P25-P32)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P25 | Warm Pad | Saws 9 cents apart, slow attack, LFO 2 breathing the filter at 0.15 Hz and swelling the level. LFO 2 → filter, its 0-to-1 form → VCA. |
| P26 | Analog Strings | PWM pulse with a saw, 250 ms attack, each voice's vibrato arriving late. LFO 2 delay and fade per voice, pedal → cutoff. |
| P27 | Slow Evolve | LFO 1 sine over 4 bars of clock on the filter, a very slow ramp on PW and resonance. 4-bar sync, the ramp-up shape. |
| P28 | Breathing Pad | Env 3 cycles freely (2.5 s up, 2 s hold, 3 s down, 2.5 s rest) opening the filter 38 semitones. A free-cycling envelope. |
| P29 | Glass Pad | Triangles a fifth apart with light poly-mod FM, resonant and slow. Aftertouch → poly-mod osc B amount. |
| P30 | Dark Choir | An 18 % pulse under PWM over a saw an octave down, low filter. LFO 2 vibrato with a long delay. |
| P31 | Touch Swell | A 1.5 s swell that shrinks to 200 ms when you hit hard. Velocity → amp and filter attack times. |
| P32 | Wide Stack | Both layers on every key, 8 cents flat and sharp, different attacks and an LFO 2 on layer 2. Stack mode, per-layer master tune. |

**Plucks (P33-P40)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P33 | Clavi | 20 % pulse with a wide pulse an octave up, a 30-semitone snap. Velocity → cutoff, VCA and filter decay; filter env → PW B. |
| P34 | Kalimba | Triangles two octaves apart, a 15 ms Env 3 click, a few cents off per note. Short Env 3, Random → pitch. |
| P35 | Harp | Saws an octave apart, long decay that shortens up the keyboard. Note → amp decay. |
| P36 | Pizzicato | 80 ms filter snap, 180 ms body: tight and dry. Velocity → VCA, cutoff and filter decay. |
| P37 | FM Bell | A triangle FM-ed by B at +22 (inharmonic), the index decaying with Env 4; long ring. Env 4 → poly-mod osc B, amp env → Pitch B. |
| P38 | Velo Pluck | No filter envelope of its own: velocity supplies the whole sweep. Velocity → filter env amount. |
| P39 | Dice Pluck | Cutoff, pulse width, decay and B's tuning rolled anew per note. Random into four destinations. |
| P40 | Marimba | Triangle with a 4:1 partial, decays that shorten up the keyboard. Note → amp decay, full keytracking. |

**Arps and sequences (P41-P48)** — hold a key (or a chord for the arp) and the patch plays; the sequence transposes
from the key. All follow the MIDI clock.

| Slot | Name | Sound and what it shows |
|---|---|---|
| P41 | Trance Bass Seq | A 16th-note octave bassline with downbeat accents; lane A opens the filter over the bar. Steps, per-step velocity, a smooth lane → cutoff. |
| P42 | Acid Seq | A 303 line: slides (gate 100 % ties), accents into the filter envelope, lane A rising. Ties, accents, mono glide. |
| P43 | Arp Up Pluck | A chord arpeggiates upward in 16ths with octave jumps on steps 5 and 8. The arpeggiator, per-step offsets. |
| P44 | Ratchet Groove | An eighth-note groove where steps stutter 2-4 times and two play only sometimes. Ratchets and probability. |
| P45 | Lane Sweep | A 32-step riff with rests; lane A glides the cutoff over two bars, lane B steps PW A every beat. Two lanes, smooth and stepped. |
| P46 | Chance Melody | A pentatonic melody where half the steps are chancy; any key stays in C minor pentatonic. Probability, scale quantize. |
| P47 | Swing Walk | An eighth-note walking bass with swing 62; the filter drops on key-up. Swing, the Gate source. |
| P48 | Chan Duo Seq | Channel 1 plays a sequenced bass (layer 1), channel 2 a legato lead (layer 2). Channel mode, a sequence on layer 1's keys only. |

**Effects (P49-P56)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P49 | S&H Sci-Fi | Sample-and-hold at 8 Hz on pitch and filter, a falling ramp on the filter. The S&H and ramp-down shapes. |
| P50 | Wind & Thunder | Noise alone through a resonant filter wandered by two drift LFOs; low keys rumble, high keys whistle. Noise, Note → cutoff. |
| P51 | Laser | Synced saws, Env 4 dropping the pitch two octaves in 300 ms. Env 4 → pitch at full depth. |
| P52 | Helicopter | A free-cycling amp envelope chops pulse, saw and noise at 12 Hz; the wheel slows the rotor. A cycling amp envelope, wheel → its rest time. |
| P53 | Siren | A triangle LFO swinging the pitch an octave each way, restarting on the first key. Full-depth pitch LFO, retrigger first. |
| P54 | Glitch Steps | LFO 1's step pattern jumps the pitch an octave, snapped to pentatonic; S&H in 16ths on the filter. The Steps shape on pitch, scale quantize. |
| P55 | Seismic Drone | Eight voices in unison 45 cents apart, drift, a per-voice drift LFO, a 2 s swell. Unison, detune, wheel → Detune. |
| P56 | Metal Clang | B at +31 modulating A and the filter through poly-mod (filter FM), resonant and inharmonic. Poly-mod into the filter. |

**Other (P57-P64)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P57 | Eight Channels | One mono voice per MIDI channel 1-8, each with its own bend, pressure and CC 74 brightness. The `8ch` layer mode. |
| P58 | MPE Glide | MPE lower zone: 48 semitones of per-note bend, slide opens the filter, pressure adds vibrato. The `mpe` layer mode. |
| P59 | Wheel Morph | A dry saw pluck that the wheel turns, knob by knob, into P25 Warm Pad; LFO 2 (1 bar) keeps it moving. The Morph destination. |
| P60 | Split Bass Lead | Split at C3: a mono bass below, a legato lead with glide, an Env 4 scoop and late vibrato above. Split mode, per-layer voice modes and routes. |
| P61 | Unison Monster | Eight voices on one note, 45 cents spread, drift, a per-voice drift LFO, legato glide. Unison, wheel → Detune. |
| P62 | Trance Gate | A saw pad cut into 16ths by LFO 1's step pattern over a bar, restarting with every chord. The Steps shape → VCA at 99, retrigger every. |
| P63 | Self Player | Hold a chord and it plays itself: synced cycling amp and filter envelopes, a per-voice S&H LFO picking pitches in C minor pentatonic. Generative. |
| P64 | Two Macros | Macro 1 (wheel, −20 offset) → cutoff, resonance, PW B; Macro 2 (pressure + CC 11) → vibrato, Pitch B, noise. Macros as sums with fan-out. |

Across the set: all eight LFO shapes (triangle, square P12, ramps P27 / P49, sine, S&H, drift, steps), clock sync in
seven patches, LFO delay and fade (P10, P15, P19, P26, P30, P48, P60), cycling envelopes free and synced (P24, P28,
P52, P63), velocity and aftertouch almost everywhere, drift (P3, P17, P23, P25, P29, P32, P55, P61), scale quantize
(P46, P54, P63), the sequencer with lanes, ties, ratchets, probability, swing and the arpeggiator, the macros, and
every layer mode: off, split (P60), stack (P32), channel (P48), 8ch (P57), mpe (P58). Sources used: LFO 1 / 2 and
their 0-to-1 forms, all four envelopes, velocity, wheel, aftertouch, random, note, the lanes, gate, timbre, bend,
pedal, CC A / B, voice and the macros (`fixed` is the only one not used).

## What the tool still needs

Two things in the files can't be sent with today's `build/pro800`; the installer skips them with a warning and
installs the rest of the patch, and a rerun fills them in once the tool has them. The web page already sends the
SysEx, so the additions are small (`tools/pro800.swift`, next to `showSequence()` and `setSetting()`), and
`plan.py` already emits the lines in this form:

1. **`pro800 seq set <key=value ...>`** — the sequence's header, SysEx `47 <length> <lanes> <division> <bpm hi>
   <bpm lo> <flags> <swing> <smooth> <arp>` (acknowledged with `7F 47`). Keys: `on` 0/1 (flag bit 0), `length`
   1-60, `step` (a division name: `1/16`, `1/8`, …, index into `divisionNames`), `clock` 0/1 (flag bit 1), `bpm`,
   `swing` 0-75, `once` 0/1 (flag bit 2), `keys` both / 1 / 2 (flag bits 3-4), `arp` off / up / down / up-down /
   random / played (0-5), `lanes` 0-4, `smooth` none or lanes `A`-`D` (bit per lane). The length is clamped to what
   the lanes leave (60, 48, 40, 34, 30). Since `47` takes every field, read the current header with `48` first and
   change only the keys given.
2. **`pro800 seq step <n> <note|.> <gate|tie> [v<vel>] [p<prob>] [x<ratchets>] [a<v>] [b<v>] [c<v>] [d<v>]`** —
   one step, SysEx `45 <n-1> 1 <note + 64> <velocity, 0 = the key's> <gate 1-99, 100 = tie> <on> <extra>
   <lane A> <lane B> <lane C> <lane D>`. `.` is a rest (on = 0). `extra` is `(8 - prob) | (ratchets - 1) << 3`
   (`p6` = 6 in 8, `x3` = three hits). Lane values −99..99 go as `64 + round(v / 99 × 63)`.
3. **`pro800 set morph <slot|none>`** — the morph target, SysEx `44 <t hi> <t lo>` with t = slot + 1 (`P25` → 25;
   0 = none), acknowledged. It saves with the patch (`patch.morph`).

`install.sh` detects them by looking for the strings `seq set` and `set morph` in `pro800 help`, so the new help
lines should contain those words.

Optional, for speed: a `pro800 batch` (or `-`) mode that reads commands from stdin over one MIDI connection would
turn the bank's 5,600 process launches into one; `install.sh` would then pipe the plan instead of looping.
