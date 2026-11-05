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
