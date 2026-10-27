// The synth engine: 8 voices, one or two layers, four ADSRs per voice, two LFOs per layer, a mod matrix, glide and
// poly-mod, all set
// from the front panel. Pitch and filter modulation are worked out in semitones and turned into CVs through the
// autotune tables, so every voice's oscillators and filter track in tune.
#ifndef SYNTH_H
#define SYNTH_H

#include <stdint.h>
#include "mod.h"
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
void synth_mod_wheel(uint8_t value); // 0-127, adds LFO depth (and is a mod source)
void synth_aftertouch(uint8_t value); // channel pressure 0-127 (a mod source)
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
// Keyboard tracking (0 off, 1 half, 2 full) is a setting now: the keyboard-tracking switch picks which envelope
// the filter ADSR knobs edit (0 = bottom: filter envelope, 1 = middle: Env 3, 2 = top: Env 4).
int synth_keytrack(void);
void synth_set_keytrack(int amount);
int synth_env_select(void);

// LFO 1 / LFO 2 (0 / 1): which one the LFO knobs, the shape button and the LFO routing buttons edit. Switching
// puts the LFO knobs in pickup.
int synth_lfo_select(void);
void synth_set_lfo_select(int which);
int synth_lfo_shape(int layer, int which); // lfo_shape_t
void synth_set_lfo_shape(int layer, int which, int shape);
// LFO 2's routing (LFO2_TO_* bits) per layer. Toggle: a routing button was toggled while LFO 2 is selected; flips
// that routing on the edit layer and keeps LFO 1's. Returns the new state, or -1 if it isn't a routing button.
int synth_lfo2_dests(int layer);
void synth_set_lfo2_dests(int layer, int bits);
int synth_lfo2_toggle(int s);

// A layer's parameter (0..1, PARAM_COUNT of them: the knobs, Env 3 / 4, LFO 2) for the host; setting one puts its
// knob in pickup.
float synth_param(int layer, int param);
void synth_set_param(int layer, int param, float value);

// Mod matrix, per layer: the depth (-99..99, 0 = no route) of source → destination; setting 0 removes a route.
// Setting returns 0 when the layer's ROUTES are all used.
int synth_route(int layer, mod_source_t source, mod_dest_t dest);
int synth_set_route(int layer, mod_source_t source, mod_dest_t dest, int depth);
const route_t *synth_routes(int layer); // ROUTES entries; source SRC_NONE = unused
// What touching a control means in MOD ASSIGN (DEST_NONE if it isn't a destination).
int synth_dest_for_pot(int pot);
int synth_dest_for_switch(int s);
// MOD ASSIGN: every knob goes into pickup (so touching one doesn't change it); a button toggled while assigning
// keeps its routing.
void synth_freeze_knobs(void);
void synth_hold_switch(int s);

// For the display: the edit layer's value of a pot (0..1), and whether the knob is still waiting to pick it up.
float synth_pot_value(int pot);
// What a knob position means for FREQ A / B (whole semitones, ±36) and FINE (cents, ±100).
int synth_knob_semitones(float knob);
int synth_knob_cents(float knob);
int synth_pot_pending(int pot);

// Patches: applying one puts every stored knob in pickup and holds each button's stored state until that button
// is toggled. Manual mode makes the whole panel live again. M VOL is never stored.
void synth_apply_patch(const patch_t *patch);
void synth_capture_patch(patch_t *patch); // fills everything but the name
void synth_manual(void);

#endif
