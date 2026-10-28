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
    // One sound, a voice per MIDI channel, each mono with its own pitch bend, pressure and CC 74:
    LAYER_VOICE_PER_CHANNEL, // channels 1-8 play voices 1-8 (bend range: the bend setting); other channels, nothing
    LAYER_MPE,               // MPE lower zone: channel 1 is the master (its bend, wheel, pressure and pedal reach
                             // every voice), channels 2-9 play voices 1-8 with 48 semitones of bend
    LAYER_MODE_COUNT
} layer_mode_t;

void synth_init(void);
void synth_note_on(uint8_t note, uint8_t velocity, int channel); // channel 0-15
// The per-channel layer modes: the voice a MIDI channel (0-15) plays, or -1 (none, or MPE's master channel), and
// what its channel's messages do to that voice.
int synth_channel_voice(int channel);
int synth_channel_plays(int channel); // 0 for a channel the mode gives nothing to play (8CH: channels 9-16)
void synth_voice_note_on(int voice, uint8_t note, uint8_t velocity);
void synth_voice_note_off(int voice, uint8_t note);
void synth_voice_bend(int voice, int value); // 14-bit, centre 8192
void synth_voice_pressure(int voice, uint8_t value);
void synth_voice_timbre(int voice, uint8_t value); // CC 74
void synth_timbre(uint8_t value, int layers);      // CC 74 for the layers a channel plays (a mod source)
// More mod sources from MIDI: the sustain pedal's state and the two assignable controllers (CC A / CC B), for the
// layers a channel plays; and which controller numbers those are (1-127, kept in the system settings)
void synth_set_pedal(int layers, int down);
void synth_cc_source(int which, uint8_t value, int layers);
int synth_cc_number(int which);
void synth_set_cc_number(int which, int number);
// MOD ASSIGN: the source touching a knob means (SRC_NONE if none): the LFO knobs the selected LFO, the ADSR knobs
// their envelope (the filter's per the keytrack switch).
int synth_source_for_pot(int pot);
// The layers (bit 0 layer 1, bit 1 layer 2) a key would play, and playing a note on given layers (the sequencer).
int synth_key_layers(uint8_t note, int channel);
void synth_note_on_layers(uint8_t note, uint8_t velocity, int layers);
// A note-off on given layers, and the layers a note-off on `channel` releases: the channel's layer in channel mode,
// else both (a key's voices stay on the layer they started on, wherever the split is now).
void synth_note_off_layers(uint8_t note, int layers);
int synth_channel_layers(int channel);
void synth_all_off(void);
// Controllers, for the layers a channel plays (synth_channel_layers(): both, but in channel mode the channel's)
void synth_pitch_bend(int value, int layers);   // 14-bit, centre 8192
void synth_mod_wheel(uint8_t value, int layers); // 0-127, adds LFO depth (and is a mod source)
void synth_aftertouch(uint8_t value, int layers); // channel pressure 0-127 (a mod source)
void synth_poly_pressure(uint8_t note, uint8_t value, int layers); // per voice: those playing the note
// Reads the panel, advances glide and drift by dt seconds, works out what the ticks build on (synth_tick()), and sets
// every CV and gate.
void synth_update(float dt);
// The fast path, run between the parts of the CV refresh: advances the envelopes and LFOs by dt and sets the VCA CVs,
// the cutoff CVs when `cutoff` and the pitch CVs of `pitch_voices` (a bitmask), with the envelopes, LFOs and the
// routes from them into pitch, cutoff and the VCA as they are now. synth_update() works out the rest once a pass.
void synth_tick(float dt, int cutoff, unsigned pitch_voices);
unsigned synth_active_voices(void);     // bitmask: voices whose amp envelope runs (the rest are silent)
unsigned synth_take_fresh_voices(void); // voices started since the last call: their pitch goes out before their VCA
// Works out the pass terms and CVs of voices that started between two ticks (a bitmask), before their pitch goes out.
void synth_prepare_voices(unsigned mask);
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

// Voice modes per layer (bits: mode | priority << 2)
enum { VOICE_POLY, VOICE_MONO, VOICE_LEGATO };
enum { PRIORITY_LAST, PRIORITY_LOW, PRIORITY_HIGH };
int synth_voice_mode(int layer);
void synth_set_voice_mode(int layer, int bits);
// Drift: slow random wander of each voice's pitch and cutoff, 0-99
int synth_drift(void);
void synth_set_drift(int amount);
// Scale quantize (bits: scale | root << 4, root 0-11 = C-B; scale 0 = off)
enum { SCALE_OFF, SCALE_MAJOR, SCALE_MINOR, SCALE_DORIAN, SCALE_PHRYGIAN, SCALE_LYDIAN, SCALE_MIXOLYDIAN,
       SCALE_HARMONIC_MINOR, SCALE_PENTATONIC_MAJOR, SCALE_PENTATONIC_MINOR, SCALE_BLUES, SCALE_WHOLE_TONE,
       SCALE_CHROMATIC, SCALE_COUNT };
int synth_scale(void);
void synth_set_scale(int bits);
// Morph: the target patch (its number + 1, 0 = none) and its knob values, which the Morph destination fades to.
// The caller loads the target's data (0 if it couldn't); applying a patch doesn't.
int synth_morph_target(void);
void synth_set_morph(int target, const patch_t *data);

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

// Cycling envelopes per layer (ENV_CYCLE_* bits): attack, hold, fall, rest, again while the key is held.
int synth_env_cycle(int layer);
void synth_set_env_cycle(int layer, int bits);
// Synced cycling per envelope (0 amp, 1 filter, 2 Env 3, 3 Env 4): 0 free, else an LFO clock division + 1. Synced,
// the cycle lasts that division and the four knobs share it out.
int synth_env_sync(int layer, int env);
void synth_set_env_sync(int layer, int env, int sync);
// For the display: the share (0..1) of a synced cycle that a knob (stage 0 attack, 1 decay = fall, 2 sustain =
// hold, 3 release = rest) at `knob` gets on the edit layer; -1 if that envelope isn't synced.
float synth_env_share(int env, int stage, float knob);
// An envelope time knob in seconds: stage 0 attack, 1 decay, 2 sustain (a time only when cycling: hold, else -1),
// 3 release (rest when cycling).
float synth_env_time(int stage, float knob, int cycling);

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
// MOD ASSIGN: on puts every knob in pickup and keeps it there, however close it is to its value, so touching one
// only chooses a destination; off lets them pick up as usual. A button toggled while assigning keeps its routing.
void synth_freeze_knobs(int on);
// A latching button's state as the engine uses it (a loaded patch's, until the button is toggled), and setting it
// from the host, which holds the same way.
int synth_switch(int s);
void synth_set_switch(int s, int on);
void synth_hold_switch(int s);

// For the display: the edit layer's value of a pot (0..1), and whether the knob is still waiting to pick it up.
float synth_pot_value(int pot);
// A stepped knob's reading (FREQ A / B; LFO FREQ while its LFO is synced) as the engine takes it: the middle of its
// step, which changes with some hysteresis against pot noise. Other knobs' readings come back as they are.
float synth_knob_snap(int pot, float knob);
// What a knob position means for FREQ A / B (whole semitones, ±36) and FINE (cents, ±100).
int synth_knob_semitones(float knob);
int synth_knob_cents(float knob);
// ... and the other knobs with units: filter env amount and poly-mod filter env (semitones), cutoff (Hz), glide
// (time constant in seconds, 0 = off). LFO rates: lfo.h.
float synth_knob_env_amount(float knob);
float synth_knob_pmod(float knob);
float synth_knob_cutoff_hz(float knob);
float synth_knob_glide_s(float knob);
// Per patch: pitch bend range (1-12 semitones) and unison detune (0-50 cents between the outermost voices).
int synth_bend_range(void);
void synth_set_bend_range(int semitones);
int synth_detune(void);
void synth_set_detune(int cents);
int synth_pot_pending(int pot);

// Patches: applying one puts every stored knob in pickup and holds each button's stored state until that button
// is toggled. Manual mode makes the whole panel live again. M VOL is never stored.
void synth_apply_patch(const patch_t *patch);
void synth_capture_patch(patch_t *patch); // fills everything but the name
void synth_manual(void);

#endif
