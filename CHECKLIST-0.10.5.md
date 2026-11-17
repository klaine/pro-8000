# 0.10.5: what to try on the synth

What the simulator can't prove: how things feel, the real pots and ADC, the real EEPROM, MIDI hardware. 0.10.5 is
0.10.0 with the bug fixes and panel requests from the issues (NOTES.md: "Bugs reported after 0.10.0", "Small panel
requests from the issues"), routes to PW past the knob's range (#64) and Drift as a mod source (#71). Tick them off as
you go; anything that doesn't behave as written here is a bug to note.

Before you start: `scripts/build.sh`, then `python tools/pro800.py install build/pro800-0.10.5.syx --yes`. A flash
restarts the synth, so save any unsaved edit first. Try sound changes on the init sound (`pro800 patch init`; save it
to P128 only if that slot holds the init sound or a test) and put back any system setting you change. If the synth
has moved since its last Tune, press Tune once it's warm: an update keeps the tuning tables.

## Tried before the version number (the same code as 0.10.5, reporting 0.10.0)

- [x] The panel fixes and requests by hand, 2026-11-12: #40 (sustain during a long decay), #41 (Shift knobs with
      pickup), #42 (knobs reach 0 and 99), the LFO shape window, #51 / #52 (VALUE's patch search with the dot),
      #54 (tempo after a tap), #55 (fast route depths), #56 (the scroll order), #58 (the Arp Assign LED), #61 (`diSP`)
      and #53 (`bEAt`).
- [x] #64 on the synth, 2026-11-17: PW 99 + a route of +99 silences the pulse; PW 0 + a route of -99 stays a thin pulse
      (the hardware's narrowest, so MANUAL promises silence at the top only).
- [x] #71 on the synth, 2026-11-17: Drift → pitch A 10 with the Drift knob at 0, the held note wanders about ±18 cents.
- [x] Tune on this machine, then `check`: oscillators within 0.9 cents from note 48 up, filters within 4.3.

## The release build (tried 2026-11-17)

- [x] `install` of 0.10.5 passes; `version` answers 0.10.5; `fault` shows no record.
- [ ] The display shows `0. 1 0. 5` for a second and a half at power-up (nobody watched it).
- [x] `timing` at 0, 1 and 8 voices on the init sound: passes of 1206 / 1420 / 2898 µs against 0.9.5's 1044 / 1254 /
      2710 (NOTES, "Timing on 0.10.5"); 0.10.0 itself was never measured with notes, so this is 0.10.0 and 0.10.5 together.
- [x] `check` within a few cents: oscillators 3.4 at worst, filters 1.9 (note 36's octave misreads aside).

## Still to try by hand

- [ ] System menu key 9 (`diSP`) set to `StAY` or seconds, and key 0 (`bEAt`) `OFF`: wait over a second, power-cycle,
      both are still set. Put them back.
- [ ] `diSP` with seconds: a knob's value stays that long, and VALUE fine-adjusts it as long as it shows; with `StAY`,
      VALUE fine-adjusts for 1.5 s, then steps the patches.
- [ ] Drift in MOD ASSIGN: VALUE scrolls to `drFt` right after `rAnd`; Arp Assign takes it; a route of 20 into the
      cutoff on a held chord: each note's brightness wanders on its own, slowly, with no steps.
- [ ] #64 by ear: an envelope into A PW from PW 70-80, the pulse thins to nothing and comes back (no click at the edge?).

## The editor (Chrome or Edge, `scripts/web.sh`)

- [ ] The header keeps its height (#57) when switching tabs and while values change.
- [ ] System tab: the Panel section shows the display hold and the beat LED and sets them.
- [ ] Mod matrix: "Drift (slow wander)" in the source list and as a Via; its Range shows both ways (it's bipolar).
- [ ] An A PW route's red arc can reach past the knob's end.
- [ ] Against 0.10.0 firmware (if you have it at hand): adding a Drift route says it needs firmware 0.10.5 or later.

## Release

- [ ] Merge into `main`, push, `scripts/pages.sh --push`, a GitHub release with `build/pro800-0.10.5.syx` and
      `build/pro8000-presets-0.10.5.json`.
- [ ] Close #40, #41, #42, #51, #52, #53, #54, #55, #56, #57, #58, #61, #64, #71; answer #60, #67, #68, #72 with what
      NOTES found; leave #43 (a question), #44, #63, #69 open.
