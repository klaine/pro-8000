// The synth engine: 8 voices, one or two layers, two ADSRs per voice, an LFO per layer, glide and poly-mod, all set
// from the front panel. Pitch and filter modulation are worked out in semitones and turned into CVs through the
// autotune tables, so every voice's oscillators and filter track in tune.
#ifndef SYNTH_H
#define SYNTH_H

#include <stdint.h>
#include "store.h"

typedef enum {
    LAYER_OFF,     // one sound on all 8 voices
    LAYER_SPLIT,   // layer 1 below the split note, layer 2 from it up; 4 voices each
    LAYER_STACK,   // every note on both layers; 4 voices each
    LAYER_CHANNEL, // MIDI channel 2 plays layer 2, every other channel layer 1; 4 voices each
    LAYER_MODE_COUNT
} layer_mode_t;

void synth_init(void);
void synth_note_on(uint8_t note, uint8_t velocity, int channel); // channel 0-15
void synth_note_off(uint8_t note);
void synth_all_off(void);
void synth_pitch_bend(int value);   // 14-bit, centre 8192
void synth_mod_wheel(uint8_t value); // 0-127, adds LFO depth
// Reads the panel, advances envelopes, LFOs and glide by dt seconds, and sets every CV and gate.
void synth_update(float dt);
int synth_any_gate(void);

layer_mode_t synth_layer_mode(void);
void synth_set_layer_mode(layer_mode_t mode);
int synth_split_note(void);
void synth_set_split_note(int note);
// The layer the per-layer knobs edit (0 or 1). Switching it puts those knobs in pickup.
int synth_edit_layer(void);
void synth_set_edit_layer(int layer);
// For the display: the edit layer's value of a pot (0..1), and whether the knob is still waiting to pick it up.
float synth_pot_value(int pot);
int synth_pot_pending(int pot);

// Patches: applying one puts every stored knob in pickup and holds each button's stored state until that button
// is toggled. Manual mode makes the whole panel live again. M VOL is never stored.
void synth_apply_patch(const patch_t *patch);
void synth_capture_patch(patch_t *patch); // fills everything but the name
void synth_manual(void);

#endif
