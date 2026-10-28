# How the bank is designed

What separates a current-sounding analog patch from an '80s demo isn't the oscillators; it's that nothing in a
modern patch holds still, no two voices are identical, and the player's hands reach more than the volume. The
PRO-800 has no effects, so all of that has to come from the engine. These are the techniques the bank uses, with
the firmware features behind them (`gen_presets.py` has them as helpers).

## Movement without drama

- **Two or three slow modulators at unrelated rates.** LFO 2 as the `drift` shape, per voice (`lfo2-trigger voice`),
  at 0.08-0.25 Hz, routed a few cents into oscillator B's pitch and a few semitones into the cutoff (`life()`), so
  every voice wanders on its own. LFO 1 at another rate on pulse width (the LFO → PW button) or the filter. Env 3 as a
  slow second filter envelope that opens the sound a second or two after the attack (`swell()`). Never the same
  cycle twice because the rates don't divide.
- **Pulse width moves.** A pulse sits near 35 %, and something slow always touches it: LFO 1, LFO 2, or a random
  offset per note. A static pulse is the surest sign of a demo patch.
- **The drift setting** (10-30) does what a real vintage poly does by accident: slow, independent pitch and cutoff
  wander per voice.

## No two voices alike

- **The voice-number source** (`spread()`): routed into oscillator B's pitch it gives each of the eight voices its
  own detune, 6-14 cents across the set, so a chord spreads like an ensemble without unison. The same source into
  the cutoff spreads that. Only per-voice destinations spread: the shared pots (mixer levels, pulse widths,
  resonance, poly-mod osc B, volume) are one wire for all voices and take their modulation from the voice evaluated
  last, so voice → pulse width is one constant offset, not a spread (the bank's first version had it on two
  patches). In the 8ch layer mode voice = MIDI channel, so the same source makes the eight channels of one patch
  differ (P57: cutoff, filter envelope amount, decays, sustains and glide graded from channel 1 to 8).
- **Random per note** into cutoff, pulse width or decay (a few units) so repeated notes differ as they do on a played
  instrument.
- **Stack mode** where one layer isn't enough: a body layer and a shine layer with different tuning, attack and
  modulation rates (Wide Lead, Poly Brass, Analog Strings, Wide Stack). The mixer, pulse widths and resonance are
  shared by the layers, so the layers differ in pitch, filter, envelopes and modulation.

## Expression

- **Velocity** (`touch()`) goes to cutoff and level on every patch, and on most to one more thing that a player
  feels: the filter envelope's amount (a harder note barks), the amp attack time (harder is faster, so a pad is a
  stab when hit), or the filter decay (harder rings longer).
- **Aftertouch** (`press()`) opens the filter, adds vibrato, widens the detune or raises the resonance, often
  through Macro 1 so one gesture moves three things.
- **Vibrato that arrives late** (`late_vibrato()`): LFO 1 as a sine with a delay and a fade-in per note, so held
  notes bloom and short notes stay clean. The mod wheel adds LFO 1 depth on every patch.
- **Legato with glide** on leads, with velocity into the glide time where it suits.

## Envelopes and the filter

- Filter envelopes have a sustain (10-60) rather than falling to zero, so held notes keep a body; the amount is
  moderate (10-30 semitones) and velocity adds the rest.
- Keytracking full on anything bright, half on basses and dark pads, so the top of the keyboard neither shrieks nor
  dulls.
- A little resonance (10-25) on almost everything; more only where it is the sound.

## Poly-mod and tuning

- **The filter envelope → Freq A amount (PMOD FIL ENV) is a pitch offset for as long as the envelope is up.** With a
  filter sustain above zero the note sits sharp by amount × sustain for its whole length (P8's first version was 0.76
  semitones sharp). Use it only with a filter sustain of 0, or for an intended pitch envelope; for a pitch kick
  that returns to zero, route Env 3 or Env 4 into Pitch A instead, with their sustain at 0.
- **Osc B → Freq A (PMOD OSC B) is exponential FM on the CV**, not linear FM: it raises the average pitch while it's
  on and doesn't make clean sidebands. Keep it to transients (Env 4 decaying to 0 into the amount) or to small
  amounts, and don't build "ratio" FM sounds on it. A held source into the amount (velocity, the wheel, pressure)
  keeps the FM on and the note sharp: P8 read 28 cents sharp from velocity → osc B amount at 20.

## What to listen for

Hold a chord on a pad and wait: it should change for the first ten seconds and keep changing. Play a lead softly and
then hard: the sound, not just the level, should answer. Play the same note eight times on a poly: the voices should
not line up.
