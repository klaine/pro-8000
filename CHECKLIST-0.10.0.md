# 0.10.0: what to try on the synth

What the simulator can't prove: how things feel, the real pots and ADC, the real EEPROM, MIDI hardware. Each step of
the 0.10.0 plan (NOTES.md, "The 0.10.0 plan") adds its items here as it lands. Tick them off as you go; anything that
doesn't behave as written here is a bug to note.

Before you start: install the build of the latest step (`python tools/pro800.py install build/pro800-0.10.0.syx --yes`
from the 0.10.0 worktree, after `scripts/build.sh`). A flash restarts the synth, so save any unsaved edit first. Try the
sound changes on P128 with the init sound (`pro800 patch init`, `pro800 patch save P128`) so your patches stay as
they are, and put back any system setting you change (MIDI channel, clock, CC A / B, pickup, the init patch).

## Step 1: the version (tried 2026-11-04)

- [x] `install` of 0.10.0 passed; `version` answers 0.10.0; no crash record; timing as 0.9.5.
- [ ] The display shows `0. 1 0. 0` for a second and a half at power-up (nobody watched it).

## Step 2: switch names and the tuning mode

- [ ] Press each waveform button: the display shows that oscillator's set for a second, saw / tri / square in fixed
      places: `A.St_` (saw and tri), `b.__q` (square alone), `A.oFF` (none). Readable? (`S` looks like a 5, `q` is a
      small q, `_` a low bar.)
- [ ] Sync, PMOD Freq A, PMOD Filter: `SY.on` / `SY.oF`, `PA.on`, `PF.on`. LFO → Freq / Filter / PW with LFO 1 selected:
      `Fr.on`, `FL.oF`, `PU.on`. With LFO 2 selected (Arp Up-Dn lit) the same texts, for LFO 2.
- [ ] Nothing shows over a menu, a save, or MOD ASSIGN, except LFO 2's routing (it always shows).
- [ ] After a patch load, press a waveform button whose position disagrees with the patch: the text says what now
      sounds.
- [ ] Hold Seq 1 and turn FREQ A: the knob's quarter picks the mode, bottom to top `SEnI`, `OCt`, `FrEE`, `FIHd`, from
      the first move. Rest the knob right on a quarter's edge: the mode doesn't flicker.

## Step 3: VALUE fine adjustment and pickup

- [ ] Turn RESONANCE, then within 1.5 s turn VALUE: each click cycle moves it a tenth (` 29.8`, ` 29.9`). Does a
      cycle per tenth feel right, too slow, too fast?
- [ ] Near self-oscillation, is a tenth of resonance audible and useful?
- [ ] After a nudge, hands off for a minute: the value stays (no creeping back to the knob, no jump from pot noise).
- [ ] Save the patch, load another, load it back, power-cycle: the tenth is still there (`pro800 params` shows the
      value; VALUE again shows the tenth).
- [ ] Touch the knob after a nudge: it takes over at once, with no dot.
- [ ] Load a patch with knobs resting near their stored values: nothing changes until you turn a knob (before 0.10.0
      a knob within 1.5 % took over untouched).
- [ ] FREQ A in semitones: store +7, set the knob to +6, load: still +7 until you turn the knob onto +7 or past it.
- [ ] FREQ A in semitones with a value at the very end (+36 or -36): turn the knob to its end. It takes over even if
      the pot doesn't quite reach the last step.
- [ ] VALUE on a stepped knob (FREQ in semitones / octaves, a synced LFO FREQ) moves one step a cycle.
- [ ] In preset mode, right after touching a knob, VALUE adjusts the knob, not the patch; 1.5 s later it steps patches
      again. Shift + VALUE still steps ten patches.

## Step 4: Perf, your init patch, the system menu

- [ ] A tap on Perf shows `HoLd` and changes nothing. Held 0.6 s, it loads the init sound. Does 0.6 s feel right?
- [ ] Hold Settings: `SEt`, then `SYS` with the Settings LED blinking. Readable as "the system menu"? Shift + Settings
      opens it at once; Settings closes it.
- [ ] System menu keys: 1 `CHnL`, 2 `P.UP`, 3 `CC.A`, 4 `CC.b`, 5 `CLoC`, 6 `tEnP` (try a tempo, and see the Sync
      Clock LED follow), 7 `InIt`. Shift + VALUE moves ten on the long pages. Put each setting back afterwards.
- [ ] Rec, a slot, Perf: `InIt.`, the patch is saved there and is now your init patch. Power-cycle at once: still set
      (system menu 7 shows it).
- [ ] From another slot, hold Perf: your init patch loads (`InIt.` with a dot), the display then shows the slot you
      were on, and Rec, Rec saves there, not over the template. Shift + Perf undoes it.
- [ ] Set the init patch to an empty slot: Perf gives the built-in sound (`InIt`, no dot).
- [ ] The editor's System tab: "Init loads" shows your choice; the top bar's Init loads what Perf loads.
- [ ] Optional: `pro800 eeprom` dump before and after setting the init patch: bytes 0x1F413 (slot + 1) and
      0x1F41C-0x1F41F (the checksum) change, nothing else in the block.

## Step 5: the editor's knobs on the display

- [ ] In Chrome / Edge, drag a knob on the Sound tab: the synth's display shows the value as the knob would, following
      the drag without lag or audio stutter (it sends every 25 ms while you drag), and goes back to the slot 1.5 s later.
- [ ] Env 3 / Env 4 knobs (the extras, or the faceplate on Env 3) show times on the synth, LFO 2's rate Hz or a
      division, whatever the synth's panel has selected (Shift, Arp Up-Dn for LFO 2).
- [ ] Loading a preset or the bank from the page doesn't flash values on the display.

## Step 6: release velocity (#8)

Needs a keyboard that sends release velocity (planist79's does), and one that doesn't if you have it. On P128's init
sound: `pro800 route 1 release-velocity amp-rel -60` (put it back with `... amp-rel 0` afterwards).

- [ ] `pro800 engine` while playing: each voice line shows the release velocity your keyboard sent, over USB (through
      the computer) and on the 5-pin DIN. Varying values, or a fixed 64 / 0?
- [ ] With rVEL → amp release -60: a quick lift gives a clearly shorter tail than a slow one.
- [ ] `pro800 route 1 release-velocity vca 99`: held notes play at full level; a soft lift ends the tail at once.
      Any click? Compare with `gate vca 99`.
- [ ] `pro800 route 1 release-velocity cutoff 40`: is the jump at key-up acceptable to the ear?
- [ ] Sustain pedal down, lift a key fast, pedal up: the tail follows that lift.
- [ ] MOD ASSIGN: Arp Assign, 4, 4: the display reads `rVEL` (the `r` and `V` glyphs).
- [ ] An arp or a sequence with rVEL → amp release and a long gate (a tie or 100 %): lifting the last key fast or slow
      changes the final tail; the steps in between all release alike. With a short gate the step has usually released
      already (at 64) when the key comes up, so the lift changes nothing there: as designed.
- [ ] A key-up Env 3 → cutoff 50 via rVEL (Env 3 key-up in the editor's extras): lift a note softly, then play the same
      note again while Env 3 still runs. The fading part jumps to the full depth at the new note (the Via is ×1 while
      a key is down). Is that jump audible enough to want it smoothed?

## Step 7: chord memory (#36)

On P128's init sound, with a MIDI keyboard (the synth has none of its own). Afterwards `pro800 patch init` and
`pro800 patch save P128` (one item saves the chord into P128), so P128 is the plain init sound once more.

- [ ] Hold C E G, press Unison: `CHrd` shows; each key now plays a major triad from it. `pro800 chord` prints
      `0 4 7 (from C4: C4 E4 G4)`. `pro800 check` still in tune.
- [ ] Unison with no key held, then with one key: `Un.on`, plain unison (all 8 voices on one note).
- [ ] A chord's voices open on their new notes with no blip of each voice's old note (play low, then a chord high).
- [ ] Level of a 4-note chord at velocity 127 against a single note: headroom, no clipping.
- [ ] `pro800 timing` while playing fast chords (and an 8-note chord): the longest pass.
- [ ] Unison on with no chord, hold D F A, press the pedal: `CHrd`, a minor triad from then on. A pedal over one key
      only sustains.
- [ ] Voice mode mono and legato (Settings key 9): the line plays chords; legato glides between them without a new
      attack.
- [ ] A one-step sequence (or an arp) over a chord: steps play the chord; the arp walks C E G from one key. `timing`.
- [ ] `pro800 route 1 mod-wheel chord-voicing 99`: rolling the wheel with a chord held moves it up the inversions
      (E G C, E C G, C G E…), at GLIDE 0 with a jump, with glide sliding. Clicks or zipper on the VCAs? `check` at
      the ladder's ends (an octave and more above the keyboard's chord) still in tune?
- [ ] A slow LFO → chord voicing at a step's edge: no flutter between two inversions.
- [ ] MOD ASSIGN: a source key, then Unison: the display reads `CH.Uo` and Unison stays as it was.
- [ ] Save to P128, power off and on: P128 loads with Unison on and plays the chord.
- [ ] The editor (Chrome / Edge): the Voice section's Chord row shows the chord and its names; typing `0 3 7` sets
      it; Capture with keys held takes them; Clear empties it. The mod matrix's Range for a route into Chord voicing
      reads in steps. Export a preset JSON: it has `"chord 1": "0 3 7"`; installing it brings the chord back.

## Step 8: every parameter by NRPN (#37)

On P128's init sound, then `pro800 patch init` and `pro800 patch save P128` afterwards. A controller that sends NRPN
(planist79's template, a BCR2000, or `pro800 nrpn`).

- [ ] `pro800 nrpn cutoff 8000`: the cutoff moves, the display shows its value as the knob would; turning CUTOFF on the
      panel picks up from there.
- [ ] From a controller, over USB and over the 5-pin DIN: a knob's coarse and fine (CC 6 then 38) with no audible step
      between them on FREQ A or the cutoff.
- [ ] `pro800 nrpn layers split`, `pro800 nrpn "env-delay amp" 40`, `pro800 nrpn "button a-saw" 0`: each takes, and the
      display shows the name (`LAYr`, `dL.A`, `A.SA`) and then the value.
- [ ] An NRPN stream with notes held: no stutter from the display.
- [ ] An MPE keyboard (or `pro800 cc 101 0` / `100 6` / `6 8` in `nPE` mode): nothing changes.
- [ ] Layer 2 linked to layer 1's filter, editing layer 2: CC 74 and the cutoff NRPN move layer 1's cutoff (heard).

## Step 9: your own CC map (#37)

The map is system-wide: afterwards `pro800 cc-map reset --yes` (or Default map on the page) and CC A back to 11.

- [ ] `pro800 cc-map`: the default list (CC 74 cutoff, 71 resonance...). A controller on CC 74 still moves the cutoff.
- [ ] `pro800 cc-map set 20 "shape 2" --layer 1`, then CC 20 swept: the display shows `SH.L2`, then the shape number, and
      LFO 2's shape steps through all nine.
- [ ] Power off and on: the map is still yours. `pro800 format --yes` isn't needed for this; if you do format, the map
      stays.
- [ ] The editor's System tab, Controllers: the map's rows, change one, Export, Default map, Import the exported file,
      Stock map (a controller template made for the stock synth then works: FREQ A on CC 8, cutoff on 15 with its fine
      part on 87).
- [ ] A map change while notes play: no glitch beyond a patch save's (the write comes a second later, once).
- [ ] Going back to stock (the installer's Empty it) with a map stored: stock boots and keeps its own settings.

## Steps 10-12: MIDI out, the THRU socket, clock out, knob moves and CC out (#38)

You need a second MIDI device on the rear THRU socket (another synth, or an interface into a DAW or a MIDI monitor),
and for some items a keyboard on MIDI IN. The settings are system-wide: put them back afterwards with
`pro800 set midi-socket thru`, `notes-out off`, `out-channel follow`, `clock-out off`, `knob-cc off`, `pc-out off`,
`cc-out-a default`, `cc-out-b default` (or the System tab's MIDI out on the page).

First, the one thing read off the schematic and never measured, `MIDI_SEL`'s level:

- [ ] Before flashing, on 0.9.5: a keyboard on IN, a receiver on THRU. Does anything come through? (Most likely not: 0.9.5
      never drives the line. Note what you see.)
- [ ] On 0.10.0 with the default `tHru` (system menu key 8, `dIn`): the keyboard's notes come through THRU. If they
      don't, and `Out` passes them instead, the level is the other way round: tell me, it's one line in `src/din.c`
      (`MIDI_SEL_THRU`).
- [ ] Power on with a MIDI monitor on THRU (`tHru` and then `Out`): nothing arrives at power-up or during the
      autotune.

The socket and notes (step 10):

- [ ] `Out`, notes out `PLAY`: the keyboard's notes don't come through THRU; the synth's notes do, on the out channel
      (`CH.ou` `FoL`: the MIDI channel, 1 with `ALL`).
- [ ] `nErG`: the keyboard's notes come through, with the synth's own messages between them (`pc-out on`, change patch
      while holding a chord on the keyboard): the receiver gets no stuck or wrong notes. A SysEx dump sent from IN
      comes out whole (with clock out off). Pull the IN cable in the middle of a dump: the synth's own notes still come
      out a moment later.
- [ ] Notes out `In` (the keys), the keyboard on IN, a DAW on USB: the DAW records the keys on the out channel; nothing goes back
      out of THRU to the keyboard's side (in `Out`).
- [ ] Notes out `PLAY`: the arp, a sequence and a chord (Unison with a chord stored) recorded in the DAW: each note once
      (unison sends one note, a chord its notes). After Seq off, a patch load, Tune and Shift + 0 (panic), nothing
      hangs in the DAW.
- [ ] Windows, no program reading the synth's port (close the DAW and the web page): play for a while, nothing slows
      down or hangs. Open the DAW: no flood of old notes, and notes (and clock, if on) come in from then on.
- [ ] Power the synth on with clock out on (Internal) and the DAW already open: the DAW gets the clock within a second or
      two of the synth's USB appearing (hold Sync Clock for a Start).
- [ ] Notes out `PLAY` on `Out`, hold a chord, then switch the socket to `tHru`: nothing hangs on the receiver. Hold a
      chord again and change the MIDI channel (Shift + NOISE) or the layer mode: nothing hangs in the DAW either.
- [ ] Program change out: loading P5 on the panel sends program change 4 to the DAW; a program change from the DAW
      loads its patch and doesn't come back.
- [ ] The system menu's key 8: `dIn`, then Arp Up-Dn through `nOtE`, `CH.ou`, `CL.ou`, `Pt.CC`, `PC.ou`, with VALUE
      changing each (MANUAL, the system menu).

Clock out (step 11):

- [ ] Clock on Internal at 120 BPM, clock out on, `Out`: a drum machine or a DAW on THRU (and the DAW over USB) reads
      120 and stays steady. Holding Sync Clock (`StAr`) restarts it on the downbeat; Shift + Sync Clock to `nIdI` stops
      it.
- [ ] Clock on MIDI, clock out on, `Out`: the DAW's clock over USB drives a drum machine on THRU through the synth. A
      clock on IN reaches the DAW over USB.
- [ ] `nErG` with the synth as the clock and a keyboard sending clock on IN: the receiver follows the synth's tempo
      only (not twice as fast, not jumping).
- [ ] SYNC IN as the source, clock out on: the receiver follows the pulses and doesn't drift against the source over a
      few minutes, also as the source's tempo changes.

Knob moves out (step 11):

- [ ] `Pt.CC` on, the DAW recording: CUTOFF goes out on CC 74, FREQ A on 14 (the CC map's numbers), on the out
      channel. After a patch load a knob sends nothing until it picks up; then it sends as it moves.
- [ ] Play the recorded CCs back with the DAW's MIDI thru on: no feedback, and the knob you move stays live. With the
      knob left alone, the DAW's automation moves the parameter again.
- [ ] Your own CC map (step 9) moves the numbers: the first CC the map has for the parameter.

CC A / B out (step 12):

- [ ] `pro800 route 1 lfo-uni cc-out-a 99` and another synth's cutoff on CC 12 (or `pro800 set cc-out-a 74`): the
      other synth's filter sweeps with the PRO-800's LFO, smoothly, from power-up, with no note played. In MOD ASSIGN
      VALUE scrolls to `CCo.A` and `CCo.b`.
- [ ] `amp-env → cc-out-b`: follows the most recent note.
- [ ] The editor's MIDI out section: CC A out set to 74 shows the warning (CC 74 is in the map); back to 12, it goes.
      CC 1, 64 and 120-127 can't be picked there, and `pro800 set cc-out-a 64` is refused.
- [ ] A fast CC out stream and notes together on THRU: the receiver's notes stay on time (CC out is at most 200
      messages a second each, a fifth of the socket).
