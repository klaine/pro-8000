# Preset bank: 66 sounds for the custom firmware

Sixty-four patches for firmware 0.8.1 and later, in slots P1-P64 (program change 0-63): 8 basses, 8 leads, 8 polys,
8 pads, 8 plucks, 8 arps and sequences, 8 effects and 8 curiosities that show the engine off. They're built for the
real PRO-800: two oscillators, the 4-pole filter, no effects, and every knob value comes from the firmware's own
curves (`MANUAL.md`: semitone steps on FREQ A / B, the squared filter-envelope and LFO curves, the exponential cutoff,
the 15 clock divisions), so `fil-env-amt 78` is +24 semitones and `cutoff 39` is 250 Hz on the display.

P65 and P66 (group `user`) are two more, made on the synth. The files hold the sounds as they are on the author's
synth (read back with the web page's "Show the current sound"), so some differ a little from what their notes
describe.

Nothing here touches the master volume. The mod wheel adds LFO 1 depth on every patch, so any patch with the
LFO → Freq button on (most leads) has wheel vibrato on top of what the notes say. Clock-synced patches run at 120 BPM
until a MIDI clock arrives.

## Install

From the web editor (any computer with Chrome or Edge): Patches tab, **Install the preset bank**, or **Install a
bank file…** with the release's `pro8000-presets-<version>.json`. The bank is these files in one, made by
`lib/bundle.py` (`scripts/web.sh` and `scripts/pages.sh` put it next to the editor as `presets.json`, and
`scripts/build.sh` writes the release copy into `build/`). With the command-line tool on macOS:

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

The web page's Patches tab reads and writes this format too (Sound as JSON): open one of these files there to load it
into the sound without the tool, or download the sound you're playing as a file like these, which `install.sh` then
installs (give it a `slot`). It adds a `firmware` key, the version it was read from, which nothing here needs.

## The files

One JSON file per patch, `<slot>-<name>.json`:

| Key | Contents |
|---|---|
| `slot`, `name`, `group` | Slot 1-128 (the bank uses 1-66), a name of up to 16 characters, one of bass / lead / poly / pad / pluck / arp / fx / other / user |
| `notes.sound`, `notes.shows` | What it sounds like, and which engine features it demonstrates |
| `buttons` | The latching buttons that are on (`a-saw` … `sync`, the tool's `set button` names); every other one is off |
| `params.shared` | The pots the layers share (mixer levels, noise, resonance, PW A / B, poly-mod osc B), 0-99 |
| `params.1`, `params.2` | Each layer's knobs, Env 3 / 4 and LFO 2, 0-99, by the tool's `param` names; layer 2 only when the patch uses it |
| `settings` | `set` commands as `"<setting> [args]": value`, e.g. `"shape 1 2": "sh"`, `"cycle-sync 1 env3": "1/4"`, `"voice 1": "legato last"`; every setting is listed, defaults included. From 0.9.0 also `"env-up 1"`, `"dahdsr 1"`, `"env-tempo 1"` (a list of envelopes, as `cycle`), `"env-curve 1 amp"` (`analog` / `linear` / `swell`), `"env-delay 1 amp"`, `"env-hold 1 amp"` (0-99), `"env-count 1 amp"` (`loop` or 1-99), `"layer-voices"` (`auto` or 1-7), and the `noise` shape; from 0.9.2 `"osc-tune 1 a"` (FREQ A / B per layer: `semitones`, `octave`, `free` or `fixed`); from 0.9.3 `"link"` (what layer 2 takes from layer 1: `none`, or a list of `amp-env`, `filter-env`, `env3`, `env4`, `lfo1`, `lfo2`, `filter`; layer 2's own values are still listed), `"lfo-trigger"` / `"lfo2-trigger"` also `one-shot` and `one-shot-voice`, and `"lfo-cycles"` / `"lfo2-cycles"` (1-99, how many cycles a one shot runs; left out, 1), and `"lfo-restart"` (`steps` or `keys`, what restarts the LFOs with a sequence on; left out, `steps`) |
| `routes.1`, `routes.2` | Mod routes as `[source, destination, depth]`, the tool's `route` names, up to 16 per layer. From 0.9.3 a fourth element `"both"` plays the route on the other layer's voices too |
| `sequence` | The step sequence (arp group and P48): `on`, `step` (a clock division), `clock`, `bpm`, `swing`, `once`, `keys` (both / 1 / 2), `arp`, `lanes`, `chord` (0-2 extra notes per step, 0.9.3; `lanes` + `chord` at most 4), `smooth` (lanes that glide, e.g. `"A"`) and `steps`, each `{note, gate, vel, prob, ratchets, a-d, n2, n3}` (`n2`, `n3`: chord notes, ±36 semitones from the step's note; `hold`: the notes that hold past the gate, 1 own + 2 `n2` + 4 `n3`) with defaults left out (`note` 0, `gate` 50, the key's velocity, always, one hit); `{"rest": true}` is a rest, `gate` 100 a tie |
| `morph` | The morph target's slot (P59 only) |

`lib/plan.py` turns the files into command lines and checks every name and range against the tool's tables
(`tools/pro800.swift`), so a typo fails before anything reaches the synth. It's what `install.sh` runs; `plan.py
presets/` prints the plan on its own.

## The patches

**Basses (P1-P8)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P1 | Deep Sub | A triangle under a hollow square an octave below, legato glide; round, with a short filter kick that velocity controls, and a slow per-voice drift so held notes breathe. Legato, half keytracking, velocity into cutoff and the kick, a little drift. |
| P2 | Acid Line | Saw and a narrow pulse through a resonant filter with a big, fast envelope; mono so every note retriggers, a short glide for slides. Accents open it and ring longer; pressure screams; the wheel is a resonance pedal. Velocity into the envelope amount, cutoff and decay; aftertouch to cutoff; wheel to resonance; mono retrigger. |
| P3 | Reese Growl | Two saws 11 cents apart, each voice a touch different, beating against a dark filter that the drift LFO keeps nudging. Pressure growls, velocity bites. Per-voice spread and drift so the beat never settles; aftertouch to cutoff and resonance. |
| P4 | Sync Punch | Oscillator A synced to B an octave below; Env 3 drops A's pitch from 9 semitones up over 250 ms: the sync punch, mono, with the punch longer and the filter brighter on hard notes. Sync swept by Env 3 into Pitch A; velocity into Env 3's decay, cutoff and level. |
| P5 | Fat Stab | Two saws an octave apart, every voice a few cents its own, a medium filter envelope with a little resonance: the workhorse for stabs and chords, alive instead of flat. Per-voice spread, drift, velocity into cutoff, the envelope amount and the VCA. |
| P6 | Clock Wobble | Saw and square an octave apart, LFO 1 (triangle) swinging the filter 30 semitones in quarter notes from the clock; the wheel speeds the wobble to 16ths, pressure adds resonance, velocity opens. Clock-synced LFO 1 (1/4) retriggered on the first key, the wheel into the LFO rate, aftertouch to resonance. |
| P7 | PWM Bass | A pulse under slow width modulation over a saw an octave down, legato; LFO 2 drifts the width per voice at its own pace, and pressure widens it. Thick without being static. LFO 1 → PW with LFO 2 (drift, per voice) also into PW A; legato glide; aftertouch to pulse width. |
| P8 | PolyMod Bass | Saw with a triangle an octave down. Poly-mod: the filter envelope kicks oscillator A's pitch at the start of each note and Env 4 lets B frequency-modulate A for a 150 ms growl; harder notes growl harder. Poly-mod with Env 4 into the osc B amount; velocity into Env 4's decay. |

**Leads (P9-P16)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P9 | Sync Lead | A synced to B, the filter envelope sweeping A 20 semitones through poly-mod, legato glide. Vibrato waits 300 ms and fades in; pressure adds more and opens the filter; harder notes sweep further. Sync and poly-mod envelope, LFO 1 delay and fade, aftertouch to vibrato and cutoff, velocity to the sweep. |
| P10 | Wide Lead | Both layers on every note: layer 1 two saws 6 cents flat, layer 2 the same 6 cents sharp with a slower attack, so every note is four oscillators drifting around each other. Vibrato fades in late; pressure spreads them further. Stack mode as a chorus; per-voice spread; LFO fade; aftertouch into B's pitch on both layers. |
| P11 | Press to Scream | Saw with a pulse an octave up, resonance high, mono. Pressure opens the filter, raises the resonance and adds vibrato all at once through Macro 1; the wheel does the same gently. Velocity sets the bite. Macro 1 fed by aftertouch and the wheel, fanning out to cutoff, resonance and LFO depth. |
| P12 | Hollow Square | Two pulses at 35 % and 62 % width, 10 cents apart, LFO 2 slowly moving both widths per voice; press for a clock-synced octave trill from LFO 1's square. Soft filter with keytracking so the top stays round. LFO 2 (sine, per voice) into both pulse widths; LFO 1 square synced to 1/8 into pitch, depth from aftertouch. |
| P13 | Scoop Whistle | Triangles an octave apart, a resonant, open filter: a whistle. Env 4 scoops each note up a semitone in 90 ms, faster on hard notes; vibrato fades in; the filter follows the key fully so high notes sing. Env 4 into pitch, velocity into Env 4's decay, LFO delay and fade, full keytracking. |
| P14 | Velo Glide | Saws an octave apart, legato with a long glide that shortens with velocity: slow slides into soft notes, snappy ones into hard. Per-voice drift keeps the two oscillators moving; pressure brightens. Velocity into glide time (negative: harder is faster), drift, aftertouch to cutoff. |
| P15 | Brass Solo | Two saws 7 cents apart, the filter opening over 60 ms into a 30-semitone envelope; velocity makes the attack snap and the sweep bigger; vibrato arrives late; CC 2 (breath) rides the cutoff for a wind player's swell. Velocity into the filter attack time, the envelope amount and level; CC B (breath) to cutoff; LFO delay and fade. |
| P16 | Human Lead | Saw and pulse, legato with glide; every note lands with a slightly different cutoff, pitch and width, as if played by hand, and the drift LFO keeps it moving while it's held. Pressure for vibrato. Random into cutoff, pitch and PW per note; the drift LFO per voice; aftertouch to vibrato. |

**Polys (P17-P24)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P17 | Vintage Poly | Saw and a width-modulated pulse, every voice a few cents and a little width its own, a soft filter that velocity opens. LFO 2 drifts each voice slowly; the drift setting adds the rest. Chords spread, nothing holds still. Per-voice spread of pitch, the drift LFO per voice, drift, velocity to cutoff and level. |
| P18 | Poly Brass | Two saws 8 cents apart on layer 1, a third saw an octave up on layer 2 at a lower level, both with a 40 ms filter attack into a 28-semitone sweep that velocity scales. Stacked, so each note is three saws; pressure swells. Stack as a layered brass; velocity into the envelope amount and attack; aftertouch to cutoff on both layers. |
| P19 | Combo Organ | Two squares an octave apart, filter wide open, instant envelopes; the filter envelope gives a 40 ms click at each key, LFO 2 a tremolo, and a slow vibrato fades in after a while. Pressure deepens the tremolo. The filter envelope as a click transient, LFO 2 into the VCA (tremolo), LFO 1 delay and fade. |
| P20 | Poly Sync | Synced saws with a 10-semitone poly-mod sweep from the filter envelope: hard-edged chords, deeper sweeps on harder notes, and every voice's sync pitch a few cents apart so the edge shimmers. Pressure pushes the sweep further. Sync and poly-mod with velocity into the poly-mod amount; per-voice spread; aftertouch into poly-mod. |
| P21 | E Piano | Soft triangles, B an octave up and quiet for the tine; a fast filter bark (+24 semitones, 300 ms decay to a closed sustain) over a long amp decay. Harder notes bark brighter and louder; a slow sine tremolo under it, deeper with pressure. Velocity into cutoff, filter envelope amount and the VCA at once; LFO 2 as tremolo with aftertouch on its depth; full keytracking; a little drift. |
| P22 | Poly Press | Two saws 10 cents apart, filter fairly closed, a slow swell from Env 3 after the attack; pressing a key opens the filter, adds resonance and widens the detune (Macro 1), so a held chord blooms under the hand. Env 3 as a slow swell; aftertouch into Macro 1 fanning to cutoff, resonance and B's pitch. |
| P23 | Old Poly | Two saws with heavy drift and a slow per-voice drift LFO on pitch and cutoff; every voice a different width and pitch offset; a soft filter. The worn-out polysynth: nothing in tune, everything pleasant. Drift at 55, per-voice spread, the drift LFO, random cutoff per note. |
| P24 | Cycling Wah | Saw and pulse through a resonant filter; Env 3 cycles in quarter notes from the clock, opening and closing the filter in a rhythmic wah while a key is held; velocity sets how far it opens, pressure the resonance. A cycling envelope synced to the clock into the cutoff; velocity into its depth via Macro 1. |

**Pads (P25-P32)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P25 | Warm Pad | Two saws 9 cents apart, each voice its own few cents, a slow attack; the filter breathes with LFO 2 (sine, 7 s) while a slower drift moves B's pitch per voice. Velocity shapes the attack: hard notes bloom faster. Two slow modulators at unrelated rates, per-voice spread, drift, velocity into the amp attack. |
| P26 | Analog Strings | Both layers: layer 1 a pulse under slow PWM with a saw 11 cents away, layer 2 an octave up and quieter with its own PWM rate, 250 ms attack. Vibrato fades in over a second; pressure deepens it. An ensemble with no chorus. Stack with two PWM rates, LFO 1 delay and fade on both layers, aftertouch to vibrato. |
| P27 | Slow Evolve | Saw with a narrow pulse an octave down; LFO 1 (sine, 4 bars of clock) sweeps the filter while LFO 2 (drift, per voice) walks the pulse width and B's pitch, and Env 3 swells the cutoff after each attack. Never the same twice. Three modulators on three timescales: a clock-synced LFO, a per-voice drift LFO, a slow Env 3. |
| P28 | Breathing Pad | Two saws, the filter nearly closed; Env 3 cycles on its own (2.5 s up, 2 s hold, 3 s down, 1 s rest) opening the filter in slow waves while a key is held, and LFO 2 drifts each voice's pitch. Pressure lifts the floor. A free-cycling envelope into the cutoff, the drift LFO per voice, aftertouch to cutoff. |
| P29 | Glass Pad | Triangles a fifth apart with a touch of poly-mod FM that Env 4 fades out over two seconds, a resonant open filter with full keytracking; each voice a few cents apart. Bell-like on the attack, glassy after. Env 4 into the poly-mod osc B amount (FM that decays), per-voice spread, the drift LFO. |
| P30 | Dark Choir | A narrow pulse (18 %) slowly width-modulated over a saw an octave down, a dark filter with half keytracking; LFO 2 (sine) moves the width while LFO 1 vibrato fades in late. Pressure opens it like a swell pedal. LFO 2 → PW on its own rate, LFO 1 delay and fade, aftertouch to cutoff. |
| P31 | Touch Swell | Two saws with a 1.5 s swell that shrinks to about 200 ms when you hit hard, so the same patch is a pad or a stab by touch; the filter follows with its own slower swell from Env 3. Per-voice spread and drift underneath. Velocity into the amp attack time (16× range), Env 3 as a second filter envelope, spread, drift. |
| P32 | Wide Stack | Both layers on every key: layer 1 two saws 8 cents flat with a slow sine on the filter, layer 2 two saws 8 cents sharp an octave up at a lower level with a drift LFO per voice; the two layers breathe at different speeds. The biggest pad in the bank; pressure swells both. Stack with unrelated modulation on each layer; aftertouch on both; velocity into both attacks. |

**Plucks (P33-P40)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P33 | Clavi | A 20 % pulse with a wide pulse an octave up, a 30-semitone filter snap that velocity scales and lengthens; a 2 ms pitch click from Env 3 for the pick. Full keytracking keeps the top bright and thin. Velocity into the envelope amount and the filter decay; Env 3 as a pitch click; keytracking. |
| P34 | Kalimba | Triangles two octaves apart with a little noise for the thumb, a 120 ms filter pluck and a 15 ms click from Env 3; every note a slightly different brightness (random) and the tine detuned per voice. Random into cutoff per note, per-voice spread, Env 3 click, noise as the transient. |
| P35 | Harp | Saws an octave apart, a 24-semitone filter pluck and a long amp decay that velocity lengthens; each voice a few cents apart so arpeggios shimmer; a slow drift LFO keeps sustained notes alive. Velocity into the filter decay and cutoff, per-voice spread, the drift LFO. |
| P36 | Pizzicato | Saw and a 30 % pulse, an 80 ms filter snap and a 180 ms body: tight and dry; velocity opens it and a touch of random cutoff keeps repeated notes from sounding like a machine. Velocity into cutoff and level; random into cutoff; half keytracking. |
| P37 | FM Bell | A triangle frequency-modulated by oscillator B at +22 semitones (an inharmonic 3.6:1), the FM index decaying with Env 4 over 900 ms and scaled by velocity; B itself silent. A long ring with a little per-voice drift. Env 4 and velocity into the poly-mod osc B amount; the amp envelope nudging B's pitch for a bell's slight droop. |
| P38 | Velo Pluck | Saws an octave apart with no filter envelope of their own: velocity supplies the pluck (cutoff, bark and decay), so a soft note is a dull tap and a hard one a bright snap. Pressure after the note lets it swell back open. Velocity into cutoff, envelope amount and decay; aftertouch to cutoff; per-voice spread. |
| P39 | Dice Pluck | Pulse and saw an octave up through a resonant pluck; cutoff, pulse width and decay change at random on every note, and LFO 2 (S&H, per voice) steps B's pitch slightly while a note rings. No two notes alike. Random into three destinations, the S&H LFO per voice into pitch, velocity on top. |
| P40 | Marimba | A triangle with a quiet square two octaves up (the bar's 4:1 partial) and a touch of noise for the mallet; a 60 ms filter pluck, a decay that shortens up the keyboard (note → decay) and lengthens with velocity. Note number into the amp decay, velocity into the decay and cutoff, noise as the mallet. |

**Arps and sequences (P41-P48)** — hold a key (or a chord for the arp) and the patch plays; the sequence transposes
from the key. All follow the MIDI clock.

| Slot | Name | Sound and what it shows |
|---|---|---|
| P41 | Trance Bass Seq | Hold a key: a 16th-note octave bassline in that key, saw plus square an octave down, with lane A opening the filter over the bar. Accents on the downbeats. The step sequencer from MIDI clock, note offsets, per-step velocity, an automation lane (Seq A) gliding into cutoff. |
| P42 | Acid Seq | A 303-style line: one saw, resonance 70, a big fast filter envelope; ties slide (gate 100 %), accented steps hit the filter harder; lane A adds a slow filter rise. Ties/slides, accents through velocity into the filter envelope amount, mono glide, a lane on cutoff. |
| P43 | Arp Up Pluck | Hold a chord: it arpeggiates upward in 16ths, every fifth and eighth step jumping an octave. Saw plus a pulse an octave up, a short pluck. The arpeggiator (up) with per-step note offsets transposing the arp note. |
| P44 | Ratchet Groove | An eighth-note bass groove where some steps stutter (2, 3 or 4 ratchets) and two of them only play sometimes (4 and 6 in 8). Ratchets and probability per step. |
| P45 | Lane Sweep | A 32-step 16th riff with rests; lane A glides the cutoff up and back over the two bars, lane B steps the pulse width every beat. Two automation lanes: one smooth (Seq A into cutoff), one stepped (Seq B into PW A). |
| P46 | Chance Melody | A 16-step eighth-note melody in minor pentatonic where half the steps play only sometimes; whatever key you hold, the scale quantizer keeps the transposed notes in C minor pentatonic. Probability per step and scale quantize (pentatonic minor, root C). |
| P47 | Swing Walk | An eighth-note walking bass with heavy swing, saw plus a triangle an octave down, a little glide; the filter drops when the keys are released. Swing, mono glide, the Gate source (1 while a key is down). |
| P48 | Chan Duo Seq | Channel mode: channel 1 plays a sequenced 16th bass (layer 1, mono), channel 2 a legato lead with vibrato (layer 2). Only layer 1's keys start the sequence. Channel layer mode, a sequence that takes layer 1's keys only, per-layer voice modes. |

**Effects (P49-P56)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P49 | S&H Sci-Fi | Pulse and saw a fifth apart through a screaming filter; LFO 1 (sample and hold at 8 Hz) throws pitch and cutoff around while LFO 2's falling ramp pulls the filter down. The wheel speeds it up. The S&H shape on pitch and filter (buttons), the ramp-down shape, the wheel into the LFO rate. |
| P50 | Wind & Thunder | Noise alone through a resonant filter wandered by two drift LFOs; low keys rumble, high keys whistle. Press to bring the storm closer. Noise as the only source, the drift shape on both LFOs, Note into cutoff, aftertouch. |
| P51 | Laser | Synced saws; Env 4 drops the pitch two octaves in 300 ms while sweeping the sync: pew. Harder notes fall slower. Env 4 into pitch at full depth (24 semitones) plus Pitch A for the sync sweep, velocity into Env 4's decay. |
| P52 | Helicopter | Low pulse, saw and noise chopped by a free-cycling amp envelope at about 12 Hz; the wheel slows the rotor, pressure opens the filter, a drift LFO moves it. A cycling amp envelope (attack, hold, fall, rest) as a chopper, the mod wheel scaling its rest time. |
| P53 | Siren | Two squares an octave apart; LFO 1's triangle swings the pitch a full octave each way at 0.5 Hz, restarting with the first key. The wheel speeds it, pressure tames it. A full-depth triangle LFO on pitch, retrigger on the first key, the wheel into the rate, aftertouch reducing depth. |
| P54 | Glitch Steps | Saw and pulse; LFO 1's step pattern (one bar of clock) jumps the pitch an octave between its high and low steps, snapped to minor pentatonic, while LFO 2 (S&H in 16ths) picks a new cutoff every step. The Steps shape on pitch (via a route), S&H synced to 1/16 on the filter, scale quantize. |
| P55 | Seismic Drone | All eight voices in unison, saws an octave apart 45 cents spread, drift on top, a slow triangle on the filter and a per-voice drift LFO on pitch; 2 s swell. The wheel spreads the detune further. Unison with detune, drift, LFO 2 drift per voice, the wheel into the Detune destination. |
| P56 | Metal Clang | Oscillator B at +31 semitones modulates both oscillator A and the filter through poly-mod (filter FM), Env 4 adding more at the start; resonant and inharmonic, with each note's partials shifted a little. Poly-mod into the filter (filter FM) as well as freq A, Env 4 adding poly-mod depth, random per-note detune of B. |

**Other (P57-P64)**

| Slot | Name | Sound and what it shows |
|---|---|---|
| P57 | Eight Channels | Eight mono lines, one per MIDI channel 1-8, and no two alike: the channel number shapes the sound, from a dark, sustained, sliding bass on channel 1 to a bright, barking, snappy pluck with no glide on channel 8, the channels between grading from one to the other (a saw with a pulse an octave down throughout). Each channel has its own bend, pressure and CC 74 brightness. Made for sequencers that drive eight lines. The 8ch layer mode; the voice-number source (voice = channel here) into cutoff, the filter envelope's amount, the sustains, decays and glide; Timbre (CC 74) and aftertouch per voice. |
| P58 | MPE Glide | MPE (lower zone): each note on channels 2-9 has 48 semitones of bend, its slide (CC 74) opens its filter and its pressure brings in vibrato. Two saws 7 cents apart, soft attack. The mpe layer mode; per-note Timbre, aftertouch and bend; per-voice LFOs. |
| P59 | Wheel Morph | A dry saw pluck that the mod wheel turns, knob by knob, into P25 Warm Pad (same oscillators, so only the sound moves); LFO 2 (one bar of clock) keeps it drifting a little between the two on its own. The Morph destination: mod wheel and a synced LFO morphing towards another patch of the set by slot number. |
| P60 | Split Bass Lead | Split at C3: below it a mono saw bass with a short filter envelope; from it up a legato lead with glide, an Env 4 scoop into each note and a late vibrato. Split mode, per-layer voice modes, envelopes and routes (Env 4 into Pitch A on the lead only). |
| P61 | Unison Monster | All eight voices on one note, saws an octave apart spread 45 cents with drift and a per-voice drift LFO; legato with glide. The wheel pulls the voices even further apart. Unison (8 voices), detune, drift, legato glide, the wheel into Detune. |
| P62 | Trance Gate | A saw pad cut into 16ths by LFO 1's step pattern over one bar of clock (the 'ta-ta-taa' gate), restarting with every chord; LFO 2 (sine, 2 bars) sweeps the filter underneath. The Steps shape into the VCA at 99 (a trance gate), retrigger on every key, LFO 2 synced to 2 bars. |
| P63 | Self Player | Hold a key or a chord and it plays itself: the amp and filter envelopes cycle in eighths from the clock, and each voice's own S&H LFO picks a new pitch every eighth, snapped to C minor pentatonic; a drift LFO moves the filter. Pressure speeds the dice. Generative: S&H LFO into pitch with scale quantize, per-voice LFOs, synced cycling amp and filter envelopes, aftertouch into the LFO rate. |
| P64 | Two Macros | Saw and pulse. Macro 1 is 'brightness': the wheel feeds it (from a hand-set -20 offset) and it opens the cutoff, adds resonance and narrows pulse B. Macro 2 is 'motion': pressure and CC A (expression, CC 11) feed it, and it brings in vibrato, detunes B and adds noise. Both macros as sums of several sources (with a per-layer offset) fanning out to three destinations each. |

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
   random / played (0-5), `lanes` 0-4, `chord` 0-2 (flag bits 5-6; the SysEx `<lanes>` counts these too, as the last
   lanes), `smooth` none or lanes `A`-`D` (bit per lane). The length is clamped to what the lanes leave (60, 48, 40, 34,
   30). Since `47` takes every field, read the current header with `48` first and
   change only the keys given.
2. **`pro800 seq step <n> <note|.> <gate|tie> [v<vel>] [p<prob>] [x<ratchets>] [a<v>] [b<v>] [c<v>] [d<v>]`** —
   one step, SysEx `45 <n-1> 1 <note + 64> <velocity, 0 = the key's> <gate 1-99, 100 = tie> <on> <extra>
   <lane A> <lane B> <lane C> <lane D>`. `.` is a rest (on = 0). `extra` is `(8 - prob) | (ratchets - 1) << 3`
   (`p6` = 6 in 8, `x3` = three hits). Lane values −99..99 go as `64 + round(v / 99 × 63)`. `n2+4` and `n3+7` are the
   chord notes, written to the last lanes as `64 + semitones` (64 = none). `h1`-`h7` are the notes that hold past the
   gate (1 own + 2 Note 2 + 4 Note 3), sent in bits 1-3 of `<on>`.
3. **`pro800 set morph <slot|none>`** — the morph target, SysEx `44 <t hi> <t lo>` with t = slot + 1 (`P25` → 25;
   0 = none), acknowledged. It saves with the patch (`patch.morph`).

`install.sh` detects them by looking for the strings `seq set` and `set morph` in `pro800 help`, so the new help
lines should contain those words.

Optional, for speed: a `pro800 batch` (or `-`) mode that reads commands from stdin over one MIDI connection would
turn the bank's 5,600 process launches into one; `install.sh` would then pipe the plan instead of looping.
