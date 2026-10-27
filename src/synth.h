// The synth engine: 8 voices (or unison), two ADSRs per voice, LFO, glide and poly-mod, all set from the front
// panel. Pitch and filter modulation are worked out in semitones and turned into CVs through the autotune tables,
// so every voice's oscillators and filter track in tune.
#ifndef SYNTH_H
#define SYNTH_H

#include <stdint.h>

void synth_init(void);
void synth_note_on(uint8_t note, uint8_t velocity);
void synth_note_off(uint8_t note);
void synth_all_off(void);
void synth_pitch_bend(int value);   // 14-bit, centre 8192
void synth_mod_wheel(uint8_t value); // 0-127, adds LFO depth
// Reads the panel, advances envelopes, LFO and glide by dt seconds, and sets every CV and gate.
void synth_update(float dt);
int synth_any_gate(void);

#endif
