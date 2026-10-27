// A minimal 8-voice engine for testing over MIDI: saw A and B, open filter, linear attack and release.
// Pitch comes from the autotune tables (tuning.c), or the nominal oscillator 1A curve until tuned.
#ifndef VOICE_H
#define VOICE_H

#include <stdint.h>

void voice_init(void);
void voice_note_on(uint8_t note);
void voice_note_off(uint8_t note);
void voice_all_off(void);
// Advances the envelopes by `ms` milliseconds and writes the VCA CVs.
void voice_update(uint32_t ms);
int voice_any_gate(void);

#endif
