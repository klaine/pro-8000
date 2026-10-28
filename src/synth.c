// Layout follows the Prophet-600 panel the PRO-800 copies (and p600fw's behaviour where it's documented):
//   oscillator A: frequency (semitone steps), saw / triangle / square with pulse width, sync to B
//   oscillator B: frequency (semitone steps) and fine, saw / triangle / square with pulse width
//   mixer: A, B and noise levels; filter: cutoff, resonance, envelope amount (bipolar), keyboard tracking
//   poly-mod: filter envelope (software, added to oscillator A's pitch) and oscillator B (analog), routed to
//   frequency A and/or the filter by the two poly-mod buttons
//   LFO 1 (lfo.c): eight shapes, steps included (the shape button steps through them), to frequency, pulse width and/or filter,
//   free or synced to MIDI clock, with retrigger modes; glide, unison, master tune and volume
//   LFO 2: the same, edited with the LFO knobs and buttons while Arp Up-Dn selects it (its routing lives here, the
//   buttons keep showing LFO 1's)
//
// Layers: two sets of the knobs that act per voice (frequencies, fine, cutoff, envelope amount, both ADSRs,
// glide, LFO rates and amounts, poly-mod envelope amount, master tune) and two LFOs each, with their own shapes and
// LFO 2 routing. Layer 1 plays on voices 1-4
// and layer 2 on voices 5-8, except with layers off, when layer 1 has all 8. Everything else is one set of
// wires for all voices on the hardware, so it's shared: mixer, resonance, pulse widths, poly-mod osc B amount
// and routing, master volume, every button, and LFO → PW (from layer 1's LFOs). After switching the edit layer,
// a per-layer knob waits until it's turned past (or onto) that layer's value before it takes over. Loading a
// patch puts every stored knob (all but M VOL) in pickup the same way.
//
// Env 3 and Env 4 (per voice) are edited with the filter ADSR knobs while the keyboard-tracking switch is in its
// middle / top position; keyboard tracking itself is a setting. The mod matrix (mod.h) adds, per voice, each
// route's source × depth to its destination; destinations that are single wires on the hardware (mixer, PW,
// resonance, poly-mod amount) take per-note sources from the layer's most recently played voice.
//
// Envelopes: every time knob follows the same curve, an exponential offset so that the bottom of the knob is the
// minimum with no dead zone (the engine updates about every 2.3 ms, so shorter times are one step anyway).
// Attack 0 / 3 ms / 17 ms / 150 ms / 1.25 s / 10 s at 0 / 10 / 25 / 50 / 75 / 100 %; decay and release 3 ms .. 20 s
// (310 ms in the middle), measured to -60 dB so the number is how long it lasts. The attack charges towards 1.5 and
// stops at 1, the concave curve of an analog envelope; decay and release are exponential. Sustain is linear.
// Cycling (per envelope and layer): attack, hold at the top (SUS knob), fall to zero (DEC), rest (REL), again,
// for as long as the key is held; on key up it falls with the DEC time. Hold and rest use the attack's range.
// Synced cycling: the cycle lasts a clock division (lfo.c's tempo) and the four knobs are weights that share it
// out (attack, hold, fall, rest in proportion). A phase drives it, so it stays on tempo however long it runs; it
// restarts at each key.
//
// Voice modes per layer: poly, mono (every new note retriggers) or legato, with last / low / high note priority;
// unison plays the layer's mono line on all its voices. Drift wanders each voice's pitch and cutoff slowly at random
// (up to 15 cents and a semitone). Scale quantize snaps a voice's pitch, the note plus its common pitch modulation
// (LFOs, routes to pitch A + B), to a scale from a root: random and LFO pitch modulation plays melodies, and keys
// off the scale snap too. Morph: a second patch's knob values (the morph target), reached by the Morph mod
// destination (0 = this patch, 1 = the target), knob by knob; buttons, routes and settings stay this patch's.
//
// Other ranges and curves are first guesses, chosen to be playable; they're constants here.
#include "synth.h"
#include "controls.h"
#include "fastmath.h"
#include "lfo.h"
#include "seq.h"
#include "sh.h"
#include "tuning.h"

#define OSC_RANGE_SEMITONES   36.0f // frequency knobs: ± this, in semitone steps
#define FINE_SEMITONES        1.0f  // oscillator B fine: ± (100 cents)
#define MASTER_TUNE_SEMITONES 1.0f  // ±
#define BEND_DEFAULT          2     // ± semitones, when the patch doesn't set it
#define CUTOFF_LOW_NOTE       12.0f // cutoff knob at minimum (about 16 Hz)
#define CUTOFF_SPAN           120.0f
#define FILTER_ENV_SEMITONES  72.0f // envelope amount at either end (squared: 25 % = 4.5, 50 % = 18 semitones)
#define PMOD_ENV_SEMITONES    48.0f // poly-mod filter envelope → frequency A, at maximum (squared: 50 % = 12)
#define LFO_PITCH_SEMITONES   12.0f
#define LFO_FILTER_SEMITONES  48.0f
#define LFO_PW_DEPTH          0.45f
#define DETUNE_DEFAULT        14    // unison: cents between the outermost voices, when the patch doesn't set it
#define POT_SMOOTHING_S       0.01f
#define PICKUP_WINDOW         0.015f // a knob this close to the stored value picks it up
// what a route of depth 99 does at full source (pitch on a squared curve: depth 10 is 0.24 semitones, 50 is 6.1)
#define MOD_PITCH_SEMITONES   24.0f
#define MOD_CUTOFF_SEMITONES  96.0f
#define MOD_PW                0.5f
#define MOD_LFO_OCTAVES       4.0f  // LFO rate

#define ENV_CURVE_OCTAVES     12.0f // the time knobs' curve: 2^12 from the bottom to the top
#define ATTACK_MAX_S          10.0f // also hold and rest, all from 0
#define DECAY_MIN_S           0.003f
#define DECAY_MAX_S           20.0f // decay and release, to -60 dB
#define ATTACK_OVERSHOOT      1.5f
// The voice VCAs are linear in amplitude above a dead zone: measured 2026-10-27, silent up to 7.3-9.0 % of the CV
// range depending on the voice (8.9 % on voice 1, fit from -37 dB to full). The envelope starts just under the
// lowest, so tails fade instead of stopping at about -20 dB; an idle voice still gets 0.
#define VCA_THRESHOLD         0.07f
#define ENV_FLOOR             0.0005f // -66 dB: a release ends here
#define CYCLE_FLOOR           0.001f  // -60 dB: a cycle's fall ends here, on time

#define DRIFT_PITCH_SEMITONES 0.15f // at a drift of 99
#define DRIFT_CUTOFF_SEMITONES 1.0f

#define LAYERS 2

typedef enum { ENV_IDLE, ENV_ATTACK, ENV_DECAY, ENV_RELEASE, ENV_HOLD, ENV_REST } env_stage_t;

typedef struct {
    env_stage_t stage;
    float level; // 0..1
    float timer; // seconds in ENV_HOLD / ENV_REST
    float phase; // synced cycling: 0..1 through the cycle
    float start; // synced cycling: the level the current attack started from
} env_t;

// One envelope's settings, worked out once per layer and pass; the envelopes advance by any dt from them (the amp
// and filter envelopes several times a pass: synth_envelopes())
typedef struct {
    float attack, decay, release; // how fast the distance left to go shrinks: log2 per second (< 0: at once)
    float sustain;
    float hold, rest;             // seconds (cycling)
    int cycle;
    int synced;                   // synced cycling: the cycle is `period` seconds, shared out as a / h / f / rest
    float period, a, h, f;
} env_rates_t;

typedef struct {
    uint8_t note;
    uint8_t gate;
    uint8_t velocity;
    uint32_t started; // allocation order
    float pitch;      // gliding note, in semitones (MIDI note numbers)
    float random;     // -1..1, new for every note
    float pressure;   // poly aftertouch, 0..1
    float drift[2], drift_to[2], drift_timer; // pitch and cutoff wander, -1..1
    env_t amp, filter, env3, env4;
    uint8_t fresh;                // started since the main loop last asked: write its pitch before its VCA opens
    float vca_mod;                // from the last synth_update(): the VCA gain besides the amp envelope, 0..1
    float cutoff_rest, cutoff_env; // and the cutoff (semitones) besides the filter envelope, and its amount
} voice_t;

static voice_t voices[VOICE_COUNT];
static uint32_t allocations, random_state = 0x2545F491u;
static float bend, mod_wheel, aftertouch; // bend -1..1
static int bend_range = BEND_DEFAULT, detune = DETUNE_DEFAULT;
static uint8_t voice_mode[LAYERS], priority[LAYERS]; // VOICE_* and PRIORITY_* (synth.h)
static int drift_amount;                             // 0..99
static uint8_t scale, scale_root;                    // SCALE_* (0 off), root 0..11 (C..B)
static float morph_param[LAYERS][PARAM_COUNT];       // the morph target's knob values
static int morph_loaded, morph_target; // the target patch + 1, 0 = none
static int last_voice[2] = {-1, -1}; // per layer: the most recently played voice, for the single-wire destinations

// the panel's pots, smoothed (0..1), and each layer's parameters (the knobs, then Env 3 and Env 4)
static float panel[POT_COUNT];
static float layer_param[LAYERS][PARAM_COUNT] = {[0][PARAM_LFO2_RATE] = 0.5f, [1][PARAM_LFO2_RATE] = 0.5f};
static int pots_primed;
static int edit_layer;
static uint8_t latched[PARAM_COUNT]; // its knob controls the value (per-layer parameters: the edit layer's)
static int8_t pickup_side[PARAM_COUNT]; // which side of the stored value the knob started on: -1, +1 (0 = unknown)
static int env_select = -1; // which envelope the filter ADSR knobs edit: 0 filter, 1 Env 3, 2 Env 4
static int lfo_select;      // which LFO the LFO knobs, the shape button and the routing buttons edit: 0 or 1
static uint8_t lfo_shape[LAYERS][2]; // lfo_shape_t, per layer and LFO
static uint8_t lfo2_dests[LAYERS];   // LFO2_TO_* bits
static uint8_t env_cycle[LAYERS];    // ENV_CYCLE_* bits
static uint8_t env_sync[LAYERS][4];  // amp, filter, Env 3, Env 4: 0 free, else the clock division + 1

// Each envelope's knobs: attack, decay, sustain, release
static const uint8_t env_params[4][4] = {
    {POT_AMP_ATT, POT_AMP_DEC, POT_AMP_SUS, POT_AMP_REL}, {POT_FIL_ATT, POT_FIL_DEC, POT_FIL_SUS, POT_FIL_REL},
    {PARAM_ENV3_ATT, PARAM_ENV3_DEC, PARAM_ENV3_SUS, PARAM_ENV3_REL},
    {PARAM_ENV4_ATT, PARAM_ENV4_DEC, PARAM_ENV4_SUS, PARAM_ENV4_REL},
};

static const uint8_t per_layer[PARAM_COUNT] = {
    [POT_FREQ_A] = 1, [POT_FREQ_B] = 1, [POT_FINE] = 1, [POT_CUTOFF] = 1, [POT_FIL_ENV_AMT] = 1,
    [POT_FIL_ATT] = 1, [POT_FIL_DEC] = 1, [POT_FIL_SUS] = 1, [POT_FIL_REL] = 1,
    [POT_AMP_ATT] = 1, [POT_AMP_DEC] = 1, [POT_AMP_SUS] = 1, [POT_AMP_REL] = 1,
    [POT_GLIDE] = 1, [POT_LFO_FREQ] = 1, [POT_LFO_AMOUNT] = 1, [POT_PMOD_FIL_ENV] = 1, [POT_MASTER_TUNE] = 1,
    [PARAM_ENV3_ATT] = 1, [PARAM_ENV3_DEC] = 1, [PARAM_ENV3_SUS] = 1, [PARAM_ENV3_REL] = 1,
    [PARAM_ENV4_ATT] = 1, [PARAM_ENV4_DEC] = 1, [PARAM_ENV4_SUS] = 1, [PARAM_ENV4_REL] = 1,
    [PARAM_LFO2_RATE] = 1, [PARAM_LFO2_AMOUNT] = 1,
};

// buttons: a loaded patch's state holds until the button is toggled
static uint8_t switch_override[SWITCH_COUNT], switch_value[SWITCH_COUNT], switch_at_load[SWITCH_COUNT];
static int keytrack_setting = 2;
static route_t routes[LAYERS][ROUTES];

static layer_mode_t layer_mode = LAYER_OFF;
static int split_note = 60;

// unison: held notes per layer, the last one sounds
#define HELD_MAX 16
static uint8_t held[LAYERS][HELD_MAX];
static int held_count[LAYERS];
static int unison;

void synth_init(void)
{
    for (int v = 0; v < VOICE_COUNT; v++) {
        voices[v] = (voice_t){0};
        voices[v].pitch = 60;
    }
    held_count[0] = held_count[1] = 0;
}

// --- layers ---

static int layer_of(int voice) { return layer_mode == LAYER_OFF || voice < VOICE_COUNT / 2 ? 0 : 1; }

static void layer_voices(int layer, int *first, int *end)
{
    if (layer_mode == LAYER_OFF) {
        *first = 0;
        *end = VOICE_COUNT;
    } else {
        *first = layer ? VOICE_COUNT / 2 : 0;
        *end = layer ? VOICE_COUNT : VOICE_COUNT / 2;
    }
}

layer_mode_t synth_layer_mode(void) { return layer_mode; }

void synth_set_layer_mode(layer_mode_t mode)
{
    if (mode == layer_mode)
        return;
    synth_all_off();
    layer_mode = mode;
}

int synth_split_note(void) { return split_note; }

void synth_set_split_note(int note) { split_note = note < 0 ? 0 : note > 127 ? 127 : note; }

int synth_edit_layer(void) { return edit_layer; }

static void unlatch(int param)
{
    latched[param] = 0;
    pickup_side[param] = 0;
}

void synth_set_edit_layer(int layer)
{
    if (layer == edit_layer)
        return;
    edit_layer = layer;
    for (int i = 0; i < PARAM_COUNT; i++)
        if (per_layer[i])
            unlatch(i);
}

// The parameter a knob edits: the filter ADSR knobs edit Env 3 / Env 4 when the switch says so, the LFO knobs
// LFO 2 while it's selected.
static int param_of_pot(int pot)
{
    if (env_select > 0 && pot >= POT_FIL_REL && pot <= POT_FIL_ATT) { // FIL REL, SUS, DEC, ATT
        static const uint8_t stage[] = {[POT_FIL_REL] = 3, [POT_FIL_SUS] = 2, [POT_FIL_DEC] = 1, [POT_FIL_ATT] = 0};
        return (env_select == 1 ? PARAM_ENV3_ATT : PARAM_ENV4_ATT) + stage[pot];
    }
    if (lfo_select && pot == POT_LFO_FREQ)
        return PARAM_LFO2_RATE;
    if (lfo_select && pot == POT_LFO_AMOUNT)
        return PARAM_LFO2_AMOUNT;
    return pot;
}

int synth_lfo_select(void) { return lfo_select; }

void synth_set_lfo_select(int which)
{
    if (which == lfo_select)
        return;
    lfo_select = which;
    unlatch(param_of_pot(POT_LFO_FREQ)); // the knobs pick up the other LFO's values
    unlatch(param_of_pot(POT_LFO_AMOUNT));
}

int synth_lfo_shape(int layer, int which) { return lfo_shape[layer][which]; }

void synth_set_lfo_shape(int layer, int which, int shape)
{
    if (shape >= 0 && shape < LFO_SHAPE_COUNT)
        lfo_shape[layer][which] = (uint8_t)shape;
}

int synth_env_cycle(int layer) { return env_cycle[layer]; }

void synth_set_env_cycle(int layer, int bits) { env_cycle[layer] = (uint8_t)(bits & 15); }

int synth_lfo2_dests(int layer) { return lfo2_dests[layer]; }

void synth_set_lfo2_dests(int layer, int bits) { lfo2_dests[layer] = (uint8_t)(bits & 7); }

int synth_lfo2_toggle(int s)
{
    unsigned bit = s == SWITCH_LFO_FREQ ? LFO2_TO_PITCH : s == SWITCH_LFO_FILTER ? LFO2_TO_FILTER
                 : s == SWITCH_LFO_PW ? LFO2_TO_PW : 0;
    if (!bit)
        return -1;
    synth_hold_switch(s); // LFO 1 keeps its routing
    lfo2_dests[edit_layer] ^= (uint8_t)bit;
    return (lfo2_dests[edit_layer] & bit) != 0;
}

float synth_param(int layer, int param) { return layer_param[per_layer[param] ? layer : 0][param]; }

void synth_set_param(int layer, int param, float value)
{
    if (param < 0 || param >= PARAM_COUNT || param == POT_MASTER_VOL)
        return;
    value = value < 0 ? 0 : value > 1 ? 1 : value;
    if (per_layer[param])
        layer_param[layer][param] = value;
    else
        layer_param[0][param] = layer_param[1][param] = value;
    if (!per_layer[param] || layer == edit_layer)
        unlatch(param); // its knob picks it up from here
}

float synth_pot_value(int pot) { return layer_param[edit_layer][param_of_pot(pot)]; }

int synth_pot_pending(int pot) { return !latched[param_of_pot(pot)]; }

int synth_keytrack(void) { return keytrack_setting; }

void synth_set_keytrack(int amount) { keytrack_setting = amount < 0 ? 0 : amount > 2 ? 2 : amount; }

int synth_env_select(void) { return env_select < 0 ? 0 : env_select; }

void synth_aftertouch(uint8_t value) { aftertouch = (float)value / 127.0f; }

void synth_poly_pressure(uint8_t note, uint8_t value)
{
    for (int v = 0; v < VOICE_COUNT; v++)
        if (voices[v].gate && voices[v].note == note)
            voices[v].pressure = (float)value / 127.0f;
}

// --- voice modes, drift, scale, morph ---

int synth_voice_mode(int layer) { return voice_mode[layer] | priority[layer] << 2; }

void synth_set_voice_mode(int layer, int bits)
{
    int mode = bits & 3, prio = bits >> 2 & 3;
    if (mode > VOICE_LEGATO || prio > PRIORITY_HIGH || (mode == voice_mode[layer] && prio == priority[layer]))
        return;
    synth_all_off();
    voice_mode[layer] = (uint8_t)mode;
    priority[layer] = (uint8_t)prio;
}

int synth_drift(void) { return drift_amount; }
void synth_set_drift(int amount) { drift_amount = amount < 0 ? 0 : amount > 99 ? 99 : amount; }

int synth_scale(void) { return scale | scale_root << 4; }
void synth_set_scale(int bits)
{
    scale = (uint8_t)((bits & 15) < SCALE_COUNT ? bits & 15 : 0);
    scale_root = (uint8_t)((bits >> 4) % 12);
}

static const uint16_t scale_masks[SCALE_COUNT] = { // bit n: n semitones above the root is in the scale
    0xFFF, 0xAB5, 0x5AD, 0x6AD, 0x5AB, 0xAD5, 0x6B5, 0x9AD, 0x295, 0x4A9, 0x4E9, 0x555, 0xFFF,
};

// The nearest pitch in the scale (semitones, MIDI note numbers)
static float quantize(float pitch)
{
    int centre = (int)(pitch + 0.5f), best = centre;
    for (int d = 0; d <= 6; d++) {
        int up = centre + d, down = centre - d;
        if (scale_masks[scale] >> ((up - scale_root + 120) % 12) & 1) { best = up; break; }
        if (scale_masks[scale] >> ((down - scale_root + 120) % 12) & 1) { best = down; break; }
    }
    return (float)best;
}

int synth_morph_target(void) { return morph_target; }

void synth_set_morph(int target, const patch_t *data)
{
    morph_target = target;
    morph_loaded = data != 0;
    if (!data)
        return;
    const patch_t *t = data;
    for (int l = 0; l < LAYERS; l++) {
        for (int i = 0; i < POT_COUNT; i++)
            morph_param[l][i] = (float)t->pots[per_layer[i] ? l : 0][i] / 65535.0f;
        for (int i = 0; i < EXTRA_PARAMS; i++)
            morph_param[l][POT_COUNT + i] = (float)t->extra[l][i] / 65535.0f;
    }
}

// --- mod matrix ---

int synth_route(int layer, mod_source_t source, mod_dest_t dest)
{
    for (int r = 0; r < ROUTES; r++)
        if (routes[layer][r].source == source && routes[layer][r].dest == dest)
            return routes[layer][r].depth;
    return 0;
}

int synth_set_route(int layer, mod_source_t source, mod_dest_t dest, int depth)
{
    if (source == SRC_NONE || source >= SRC_COUNT || dest >= DEST_COUNT)
        return 0;
    depth = depth < -99 ? -99 : depth > 99 ? 99 : depth;
    int free_slot = -1;
    for (int r = 0; r < ROUTES; r++) {
        route_t *x = &routes[layer][r];
        if (x->source == source && x->dest == dest) {
            if (depth)
                x->depth = (int8_t)depth;
            else
                *x = (route_t){0};
            return 1;
        }
        if (x->source == SRC_NONE && free_slot < 0)
            free_slot = r;
    }
    if (!depth)
        return 1;
    if (free_slot < 0)
        return 0;
    routes[layer][free_slot] = (route_t){(uint8_t)source, (uint8_t)dest, (int8_t)depth};
    return 1;
}

const route_t *synth_routes(int layer) { return routes[layer]; }

int synth_dest_for_pot(int pot)
{
    switch (pot) {
    case POT_FREQ_A: return DEST_PITCH_A;
    case POT_FREQ_B: case POT_FINE: return DEST_PITCH_B;
    case POT_MASTER_TUNE: return DEST_PITCH;
    case POT_CUTOFF: return DEST_CUTOFF;
    case POT_FIL_ENV_AMT: return DEST_FIL_ENV_AMT;
    case POT_PMOD_FIL_ENV: return DEST_PMOD_ENV;
    case POT_LFO_AMOUNT: return lfo_select ? DEST_LFO2_AMOUNT : DEST_LFO_AMOUNT;
    case POT_LFO_FREQ: return lfo_select ? DEST_LFO2_RATE : DEST_LFO_RATE;
    case POT_AMP_SUS: return DEST_VCA;
    case POT_A_LEVEL: return DEST_A_LEVEL;
    case POT_B_LEVEL: return DEST_B_LEVEL;
    case POT_NOISE: return DEST_NOISE;
    case POT_RESONANCE: return DEST_RESONANCE;
    case POT_A_PW: return DEST_A_PW;
    case POT_B_PW: return DEST_B_PW;
    case POT_PMOD_OSC_B: return DEST_PMOD_OSC_B;
    default: return DEST_NONE;
    }
}

int synth_dest_for_switch(int s)
{
    switch (s) {
    case SWITCH_LFO_FREQ: return lfo_select ? DEST_LFO2_TO_PITCH : DEST_LFO_TO_PITCH;
    case SWITCH_LFO_FILTER: return lfo_select ? DEST_LFO2_TO_FILTER : DEST_LFO_TO_FILTER;
    case SWITCH_LFO_PW: return lfo_select ? DEST_LFO2_TO_PW : DEST_LFO_TO_PW;
    default: return DEST_NONE;
    }
}

void synth_freeze_knobs(void)
{
    for (int i = 0; i < PARAM_COUNT; i++)
        if (i != POT_MASTER_VOL)
            unlatch(i);
}

static int sw(switch_t s)
{
    int physical = controls_on(s);
    if (switch_override[s] && physical != switch_at_load[s])
        switch_override[s] = 0; // toggled since the patch was loaded
    return switch_override[s] ? switch_value[s] : physical;
}

int synth_switch(int s) { return s >= 0 && s < SWITCH_COUNT ? sw((switch_t)s) : 0; }

void synth_set_switch(int s, int on)
{
    // like a loaded patch: holds until the button is toggled
    if (s < 0 || s >= SWITCH_COUNT)
        return;
    switch_value[s] = (uint8_t)(on != 0);
    switch_at_load[s] = (uint8_t)controls_on((switch_t)s);
    switch_override[s] = 1;
}

void synth_hold_switch(int s)
{
    // the button was just toggled: keep the state it had (the held one, if it was already holding a patch's or an
    // earlier hold's state), until it's toggled again
    int now = controls_on((switch_t)s);
    if (!switch_override[s])
        switch_value[s] = (uint8_t)!now;
    switch_at_load[s] = (uint8_t)now;
    switch_override[s] = 1;
}

void synth_apply_patch(const patch_t *patch)
{
    synth_all_off();
    for (int l = 0; l < LAYERS; l++) {
        for (int i = 0; i < POT_COUNT; i++)
            if (i != POT_MASTER_VOL)
                layer_param[l][i] = (float)patch->pots[per_layer[i] ? l : 0][i] / 65535.0f;
        for (int i = 0; i < EXTRA_PARAMS; i++)
            layer_param[l][POT_COUNT + i] = (float)patch->extra[l][i] / 65535.0f;
        for (int r = 0; r < ROUTES; r++) {
            route_t x = patch->routes[l][r];
            routes[l][r] = x.source < SRC_COUNT && x.dest < DEST_COUNT ? x : (route_t){0};
        }
        for (int w = 0; w < 2; w++)
            lfo_shape[l][w] = patch->lfo_shape[l][w] < LFO_SHAPE_COUNT ? patch->lfo_shape[l][w] : 0;
        lfo2_dests[l] = patch->lfo2_dests[l] & 7;
        env_cycle[l] = patch->env_cycle[l] & 15;
        for (int e = 0; e < 4; e++)
            env_sync[l][e] = patch->env_sync[l][e] <= LFO_DIVISIONS ? patch->env_sync[l][e] : 0;
        for (int w = 0; w < 2; w++) { // no steps stored (older patches): the default pattern
            int count = patch->step_count[l][w];
            lfo_step_count[LFO_ID(l, w)] = (uint8_t)(count >= 1 && count <= LFO_MAX_STEPS ? count : LFO_DEFAULT_STEPS);
            lfo_pattern[LFO_ID(l, w)] = count ? patch->step_pattern[l][w] : LFO_DEFAULT_PATTERN;
        }
    }
    for (int i = 0; i < PARAM_COUNT; i++) {
        latched[i] = i == POT_MASTER_VOL;
        pickup_side[i] = 0;
    }
    for (int s = 0; s < SWITCH_COUNT; s++) {
        switch_value[s] = (patch->switches >> s) & 1;
        switch_at_load[s] = (uint8_t)controls_on((switch_t)s);
        switch_override[s] = s <= SWITCH_UNISON || s == SWITCH_SYNC;
    }
    keytrack_setting = patch->keytrack <= 2 ? patch->keytrack : 0;
    layer_mode = patch->layer_mode < LAYER_MODE_COUNT ? (layer_mode_t)patch->layer_mode : LAYER_OFF;
    synth_set_split_note(patch->split);
    for (int w = 0; w < 2; w++) {
        lfo_sync[w] = patch->lfo_sync[w] < LFO_SYNC_COUNT ? (lfo_sync_t)patch->lfo_sync[w] : LFO_SYNC_FREE;
        lfo_trigger[w] = patch->lfo_trigger[w] < LFO_TRIGGER_COUNT ? (lfo_trigger_t)patch->lfo_trigger[w]
                                                                     : LFO_TRIGGER_FREE;
    }
    unison = sw(SWITCH_UNISON);
    for (int l = 0; l < LAYERS; l++) {
        voice_mode[l] = patch->voice_mode >> (4 * l) & 3;
        priority[l] = patch->voice_mode >> (4 * l + 2) & 3;
        if (voice_mode[l] > VOICE_LEGATO) voice_mode[l] = VOICE_POLY;
        if (priority[l] > PRIORITY_HIGH) priority[l] = PRIORITY_LAST;
    }
    synth_set_drift(patch->drift);
    synth_set_scale(patch->scale);
    bend_range = patch->bend_range ? patch->bend_range : BEND_DEFAULT; // 0: not stored (before 0.7.1)
    detune = patch->detune ? patch->detune - 1 : DETUNE_DEFAULT;
    synth_set_bend_range(bend_range);
    synth_set_detune(detune);
}

void synth_capture_patch(patch_t *patch)
{
    for (int l = 0; l < LAYERS; l++) {
        for (int i = 0; i < PARAM_COUNT; i++) {
            float v = layer_param[per_layer[i] ? l : 0][i];
            uint16_t u = (uint16_t)(v <= 0 ? 0 : v >= 1 ? 65535 : v * 65535.0f + 0.5f);
            if (i < POT_COUNT)
                patch->pots[l][i] = u;
            else
                patch->extra[l][i - POT_COUNT] = u;
        }
        for (int r = 0; r < ROUTES; r++)
            patch->routes[l][r] = routes[l][r];
        for (int w = 0; w < 2; w++)
            patch->lfo_shape[l][w] = lfo_shape[l][w];
        patch->lfo2_dests[l] = lfo2_dests[l];
        patch->env_cycle[l] = env_cycle[l];
        for (int e = 0; e < 4; e++)
            patch->env_sync[l][e] = env_sync[l][e];
        for (int w = 0; w < 2; w++) {
            patch->step_count[l][w] = lfo_step_count[LFO_ID(l, w)];
            patch->step_pattern[l][w] = lfo_pattern[LFO_ID(l, w)];
        }
    }
    patch->switches = 0;
    for (int s = 0; s < SWITCH_COUNT; s++)
        if ((s <= SWITCH_UNISON || s == SWITCH_SYNC) && sw((switch_t)s)) // the latching buttons only
            patch->switches |= 1u << s;
    patch->keytrack = (uint8_t)keytrack_setting;
    patch->layer_mode = (uint8_t)layer_mode;
    patch->split = (uint8_t)split_note;
    patch->voice_mode = (uint8_t)(voice_mode[0] | priority[0] << 2 | voice_mode[1] << 4 | priority[1] << 6);
    patch->drift = (uint8_t)drift_amount;
    patch->morph = (uint8_t)morph_target;
    patch->scale = (uint8_t)synth_scale();
    patch->bend_range = (uint8_t)bend_range;
    patch->detune = (uint8_t)(detune + 1); // 0 means "not stored"
    for (int w = 0; w < 2; w++) {
        patch->lfo_sync[w] = (uint8_t)lfo_sync[w];
        patch->lfo_trigger[w] = (uint8_t)lfo_trigger[w];
    }
}

void synth_manual(void)
{
    for (int i = 0; i < PARAM_COUNT; i++)
        if (i < POT_COUNT || !per_layer[i])
            latched[i] = 1; // Env 3 / Env 4 keep their values until their knobs are selected and moved
    for (int s = 0; s < SWITCH_COUNT; s++)
        switch_override[s] = 0;
}

// --- notes ---

// Within one layer's voices: the voice already on this note, then a silent one, then the oldest released, then
// the oldest.
static int allocate(int layer, uint8_t note)
{
    int first, end, best = -1;
    layer_voices(layer, &first, &end);
    for (int v = first; v < end; v++)
        if (voices[v].note == note && (voices[v].gate || voices[v].amp.stage != ENV_IDLE))
            return v;
    for (int pass = 0; pass < 3 && best < 0; pass++)
        for (int v = first; v < end; v++) {
            voice_t *x = &voices[v];
            int ok = pass == 0 ? !x->gate && x->amp.stage == ENV_IDLE : pass == 1 ? !x->gate : 1;
            if (ok && (best < 0 || x->started < voices[best].started))
                best = v;
        }
    return best;
}

static float random_signed(void) // xorshift32, -1..1
{
    random_state ^= random_state << 13;
    random_state ^= random_state >> 17;
    random_state ^= random_state << 5;
    return (float)(int32_t)random_state / 2147483648.0f;
}

static void gate_on(voice_t *x, uint8_t note, uint8_t velocity, int retrigger)
{
    int was_silent = x->amp.stage == ENV_IDLE;
    x->note = note;
    x->gate = 1;
    x->velocity = velocity;
    x->started = ++allocations;
    x->random = random_signed();
    x->pressure = 0;
    if (was_silent)
        x->pitch = note; // glide only from a sounding note
    if (retrigger || was_silent)
        x->fresh = 1;
    if (retrigger) {
        x->amp.stage = ENV_ATTACK; // from the current level, so a stolen voice doesn't click
        x->filter.stage = ENV_ATTACK;
        x->env3.stage = ENV_ATTACK;
        x->env4.stage = ENV_ATTACK;
        env_t *envs[] = {&x->amp, &x->filter, &x->env3, &x->env4};
        for (int i = 0; i < 4; i++) { // synced cycling starts its cycle here
            envs[i]->phase = 0;
            envs[i]->start = envs[i]->level;
        }
    }
}

static void gate_off(voice_t *x)
{
    x->gate = 0;
    env_t *envs[] = {&x->amp, &x->filter, &x->env3, &x->env4};
    for (int i = 0; i < 4; i++)
        if (envs[i]->stage != ENV_IDLE)
            envs[i]->stage = ENV_RELEASE;
}

static int layer_gated(int layer)
{
    int first, end;
    layer_voices(layer, &first, &end);
    for (int v = first; v < end; v++)
        if (voices[v].gate)
            return 1;
    return 0;
}

static void remove_held(int layer, uint8_t note)
{
    for (int i = 0; i < held_count[layer]; i++)
        if (held[layer][i] == note) {
            for (int j = i; j < held_count[layer] - 1; j++)
                held[layer][j] = held[layer][j + 1];
            held_count[layer]--;
            return;
        }
}

// Mono, legato and unison play one line: the held note the priority picks
static int mono(int layer) { return unison || voice_mode[layer] != VOICE_POLY; }

static uint8_t priority_note(int layer)
{
    uint8_t n = held[layer][held_count[layer] - 1]; // the latest
    for (int i = 0; i < held_count[layer]; i++) {
        uint8_t h = held[layer][i];
        if ((priority[layer] == PRIORITY_LOW && h < n) || (priority[layer] == PRIORITY_HIGH && h > n))
            n = h;
    }
    return n;
}

static void mono_voices(int layer, int *first, int *end)
{
    layer_voices(layer, first, end);
    if (!unison)
        *end = *first + 1; // mono / legato: the layer's first voice
}

static void layer_note_on(int layer, uint8_t note, uint8_t velocity)
{
    int first, end;
    layer_voices(layer, &first, &end);
    if (mono(layer)) {
        int was_held = held_count[layer] > 0;
        remove_held(layer, note); // moves to the top if already held
        if (held_count[layer] == HELD_MAX)
            remove_held(layer, held[layer][0]);
        held[layer][held_count[layer]++] = note;
        uint8_t target = priority_note(layer);
        mono_voices(layer, &first, &end);
        if (was_held && voices[first].gate && voices[first].note == target)
            return; // the priority keeps the note that's sounding
        // mono retriggers every new note; legato and unison only the first after all were released
        int retrigger = !was_held || (voice_mode[layer] == VOICE_MONO);
        for (int v = first; v < end; v++) {
            gate_on(&voices[v], target, velocity, retrigger);
            lfo_note_on(LFO_ID(layer, 0), v, !was_held);
            lfo_note_on(LFO_ID(layer, 1), v, !was_held);
        }
        last_voice[layer] = first;
        return;
    }
    int was_gated = layer_gated(layer);
    int v = allocate(layer, note);
    gate_on(&voices[v], note, velocity, 1);
    lfo_note_on(LFO_ID(layer, 0), v, !was_gated);
    lfo_note_on(LFO_ID(layer, 1), v, !was_gated);
    last_voice[layer] = v;
}

int synth_key_layers(uint8_t note, int channel)
{
    switch (layer_mode) {
    case LAYER_SPLIT: return note < split_note ? 1 : 2;
    case LAYER_STACK: return 3;
    case LAYER_CHANNEL: return channel == 1 ? 2 : 1;
    default: return 1;
    }
}

void synth_note_on_layers(uint8_t note, uint8_t velocity, int layers)
{
    if (layers & 1)
        layer_note_on(0, note, velocity);
    if (layers & 2 && layer_mode != LAYER_OFF)
        layer_note_on(1, note, velocity);
}

void synth_note_on(uint8_t note, uint8_t velocity, int channel)
{
    synth_note_on_layers(note, velocity, synth_key_layers(note, channel));
}

void synth_note_off(uint8_t note)
{
    for (int layer = 0; layer < LAYERS; layer++) {
        int first, end;
        layer_voices(layer, &first, &end);
        if (mono(layer)) {
            int was_held = held_count[layer] > 0;
            remove_held(layer, note);
            if (!was_held)
                continue;
            mono_voices(layer, &first, &end);
            for (int v = first; v < end; v++) {
                if (!held_count[layer])
                    gate_off(&voices[v]);
                else if (voices[v].note != priority_note(layer)) { // back to a held note: legato, or mono retriggers
                    if (voice_mode[layer] == VOICE_MONO)
                        gate_on(&voices[v], priority_note(layer), voices[v].velocity, 1);
                    else
                        voices[v].note = priority_note(layer);
                }
            }
            continue;
        }
        for (int v = first; v < end; v++)
            if (voices[v].gate && voices[v].note == note)
                gate_off(&voices[v]);
    }
}

void synth_all_off(void)
{
    held_count[0] = held_count[1] = 0;
    for (int v = 0; v < VOICE_COUNT; v++)
        gate_off(&voices[v]);
}

void synth_pitch_bend(int value) { bend = (float)(value - 8192) / 8192.0f; }

int synth_bend_range(void) { return bend_range; }
void synth_set_bend_range(int semitones) { bend_range = semitones < 1 ? 1 : semitones > 12 ? 12 : semitones; }
int synth_detune(void) { return detune; }
void synth_set_detune(int cents) { detune = cents < 0 ? 0 : cents > 50 ? 50 : cents; }

void synth_mod_wheel(uint8_t value) { mod_wheel = (float)value / 127.0f; }

int synth_any_gate(void)
{
    for (int v = 0; v < VOICE_COUNT; v++)
        if (voices[v].gate)
            return 1;
    return 0;
}

// --- modulation ---

// A time knob (0..1) → seconds, from `min` to `max` along the curve described at the top
static float knob_time(float knob, float min, float max)
{
    static const float span = 4095.0f; // 2^ENV_CURVE_OCTAVES - 1
    return min + (max - min) * (fast_exp2(knob * ENV_CURVE_OCTAVES) - 1.0f) / span;
}

float synth_env_time(int stage, float knob, int cycling)
{
    switch (stage) {
    case 0: return knob_time(knob, 0, ATTACK_MAX_S);                                // attack
    case 1: return knob_time(knob, DECAY_MIN_S, DECAY_MAX_S);                       // decay
    case 2: return cycling ? knob_time(knob, 0, ATTACK_MAX_S) : -1;                 // sustain, or hold
    default: return cycling ? knob_time(knob, 0, ATTACK_MAX_S) : knob_time(knob, DECAY_MIN_S, DECAY_MAX_S);
    }
}

// The rate that covers a stage of `time` seconds: an attack (from 0 to 1 on its way to 1.5: 1/3 of the distance
// left at the end) or a fall (to -60 dB: 1/1000 left). step() turns it into the multiplier for dt.
static float stage_rate(float time, float log2_left)
{
    return time <= 0 ? -1.0f : log2_left / time;
}

static float step(float rate, float dt)
{
    return rate < 0 ? 0 : fast_exp2(-dt * rate);
}

// A synced cycle's shares of its period, from the knobs as weights (attack, hold = SUS, fall = DEC, rest = REL)
static void cycle_shares(const float *p, const uint8_t *knobs, float share[4])
{
    float w[4] = {p[knobs[0]], p[knobs[2]], p[knobs[1]], p[knobs[3]]};
    float sum = w[0] + w[1] + w[2] + w[3];
    for (int i = 0; i < 4; i++)
        share[i] = sum < 0.001f ? 0.25f : w[i] / sum;
}

static void env_rates(env_rates_t *r, const float *p, const uint8_t *knobs, int cycle, int sync)
{
    int att = knobs[0], dec = knobs[1], sus = knobs[2], rel = knobs[3];
    r->cycle = cycle;
    r->attack = stage_rate(synth_env_time(0, p[att], cycle), 1.585f); // log2(3)
    r->decay = stage_rate(synth_env_time(1, p[dec], cycle), 9.966f);  // log2(1000)
    r->release = cycle ? r->decay : stage_rate(synth_env_time(3, p[rel], 0), 9.966f);
    r->sustain = p[sus];
    r->hold = cycle ? synth_env_time(2, p[sus], 1) : 0;
    r->rest = cycle ? synth_env_time(3, p[rel], 1) : 0;
    r->synced = cycle && sync > 0;
    if (r->synced) {
        float share[4];
        cycle_shares(p, knobs, share);
        r->period = lfo_division_quarters(sync - 1) * lfo_quarter_seconds();
        r->a = share[0];
        r->h = share[1];
        r->f = share[2];
        r->release = stage_rate(r->f * r->period, 9.966f); // key up: falls with its share of the cycle
    }
}

// Synced cycling: the level is a function of the phase through the cycle
static void env_synced(env_t *e, float dt, const env_rates_t *r)
{
    e->phase += dt / r->period;
    if (e->phase >= 1.0f) {
        e->phase -= (float)(int)e->phase;
        e->start = 0;
    }
    float x = e->phase;
    if (x < r->a) {
        e->stage = ENV_ATTACK;
        e->level = e->start + (1.0f - e->start) * ATTACK_OVERSHOOT * (1.0f - fast_exp2(-x / r->a * 1.585f));
    } else if ((x -= r->a) < r->h) {
        e->stage = ENV_HOLD;
        e->level = 1.0f;
    } else if ((x -= r->h) < r->f) {
        e->stage = ENV_DECAY;
        e->level = fast_exp2(-x / r->f * 9.966f);
    } else {
        e->stage = ENV_REST;
        e->level = 0;
    }
}

int synth_env_sync(int layer, int env) { return env_sync[layer][env]; }

void synth_set_env_sync(int layer, int env, int sync)
{
    if (sync >= 0 && sync <= LFO_DIVISIONS)
        env_sync[layer][env] = (uint8_t)sync;
}

float synth_env_share(int env, int stage, float knob)
{
    if (!(env_cycle[edit_layer] >> env & 1) || !env_sync[edit_layer][env])
        return -1;
    float p[PARAM_COUNT];
    for (int i = 0; i < PARAM_COUNT; i++)
        p[i] = layer_param[edit_layer][i];
    p[env_params[env][stage]] = knob;
    float share[4];
    cycle_shares(p, env_params[env], share);
    static const uint8_t slot[4] = {0, 2, 1, 3}; // attack, decay = fall, sustain = hold, release = rest
    return share[slot[stage]];
}

static void env_update(env_t *e, float dt, const env_rates_t *r)
{
    if (r->synced && e->stage != ENV_IDLE && e->stage != ENV_RELEASE) {
        env_synced(e, dt, r);
        return;
    }
    switch (e->stage) {
    case ENV_IDLE:
        e->level = 0;
        break;
    case ENV_ATTACK:
        e->level = ATTACK_OVERSHOOT - (ATTACK_OVERSHOOT - e->level) * step(r->attack, dt);
        if (e->level >= 1.0f) {
            e->level = 1.0f;
            e->stage = r->cycle ? ENV_HOLD : ENV_DECAY;
            e->timer = 0;
        }
        break;
    case ENV_HOLD:
        e->timer += dt;
        if (e->timer >= r->hold || !r->cycle)
            e->stage = ENV_DECAY;
        break;
    case ENV_DECAY:
        if (!r->cycle) { // then follows the sustain knob while held
            e->level = r->sustain + (e->level - r->sustain) * step(r->decay, dt);
            break;
        }
        e->level *= step(r->decay, dt);
        if (e->level < CYCLE_FLOOR) {
            e->level = 0;
            e->stage = ENV_REST;
            e->timer = 0;
        }
        break;
    case ENV_REST:
        e->timer += dt;
        if (!r->cycle)
            e->stage = ENV_DECAY; // cycling switched off: settle on the sustain level
        else if (e->timer >= r->rest)
            e->stage = ENV_ATTACK;
        break;
    case ENV_RELEASE:
        e->level *= step(r->release, dt);
        if (e->level < ENV_FLOOR) {
            e->level = 0;
            e->stage = ENV_IDLE;
        }
        break;
    }
}

static uint16_t cv_from(float x)
{
    return (uint16_t)(x <= 0 ? 0 : x >= 1 ? 65535 : x * 65535.0f);
}

static float semitone_steps(float knob)
{
    return (float)(int)((knob - 0.5f) * 2 * OSC_RANGE_SEMITONES + OSC_RANGE_SEMITONES + 0.5f) - OSC_RANGE_SEMITONES;
}

int synth_knob_semitones(float knob) { return (int)semitone_steps(knob); }

static float env_amount_of(float knob) { float x = (knob - 0.5f) * 2; return x * (x < 0 ? -x : x); } // -1..1, squared
float synth_knob_env_amount(float knob) { return env_amount_of(knob) * FILTER_ENV_SEMITONES; }
float synth_knob_pmod(float knob) { return knob * knob * PMOD_ENV_SEMITONES; }
float synth_knob_cutoff_hz(float knob) { return 440.0f * fast_exp2((CUTOFF_LOW_NOTE + knob * CUTOFF_SPAN - 69.0f) / 12.0f); }
float synth_knob_glide_s(float knob) { return knob < 0.02f ? 0 : 0.002f * fast_exp2(knob * 10.0f); }

int synth_knob_cents(float knob)
{
    float cents = (knob - 0.5f) * 2 * FINE_SEMITONES * 100;
    return (int)(cents < 0 ? cents - 0.5f : cents + 0.5f);
}

static void read_panel(float dt)
{
    int select = controls_keytrack(); // the switch picks the envelope the filter ADSR knobs edit
    if (select != env_select) {
        int first_time = env_select < 0;
        env_select = select;
        for (int i = POT_FIL_REL; i <= POT_FIL_ATT && !first_time; i++) {
            latched[param_of_pot(i)] = 0;
            pickup_side[param_of_pot(i)] = 0;
        }
    }

    float k = dt >= POT_SMOOTHING_S || !pots_primed ? 1.0f : dt / POT_SMOOTHING_S;
    for (int i = 0; i < POT_COUNT; i++) {
        panel[i] += ((float)controls_pot(i) / 4095.0f - panel[i]) * k;
        int p = param_of_pot(i);
        if (!pots_primed) { // both layers start from the panel
            layer_param[0][p] = layer_param[1][p] = panel[i];
            latched[p] = 1;
            continue;
        }
        int layer = per_layer[p] ? edit_layer : 0; // shared knobs keep one value, in both layers
        if (!latched[p]) {
            float diff = panel[i] - layer_param[layer][p];
            int8_t side = diff < 0 ? -1 : 1;
            if (diff < PICKUP_WINDOW && diff > -PICKUP_WINDOW)
                latched[p] = 1;
            else if (pickup_side[p] == 0)
                pickup_side[p] = side;
            else if (side != pickup_side[p])
                latched[p] = 1; // swept past it
        }
        if (latched[p]) {
            if (per_layer[p])
                layer_param[edit_layer][p] = panel[i];
            else
                layer_param[0][p] = layer_param[1][p] = panel[i];
        }
    }
    pots_primed = controls_ready(); // until the first full scan, keep copying the panel into both layers

    int now_unison = sw(SWITCH_UNISON);
    if (now_unison != unison) {
        synth_all_off();
        unison = now_unison;
    }
}

static float source_value(const voice_t *x, int voice, int layer, int source)
{
    switch (source) {
    case SRC_LFO: return lfo_value(LFO_ID(layer, 0), voice, (lfo_shape_t)lfo_shape[layer][0]);
    case SRC_LFO2: return lfo_value(LFO_ID(layer, 1), voice, (lfo_shape_t)lfo_shape[layer][1]);
    case SRC_FILTER_ENV: return x->filter.level;
    case SRC_AMP_ENV: return x->amp.level;
    case SRC_VELOCITY: return (float)x->velocity / 127.0f;
    case SRC_MOD_WHEEL: return mod_wheel;
    case SRC_AFTERTOUCH: return x->pressure > aftertouch ? x->pressure : aftertouch; // poly, else channel
    case SRC_ENV3: return x->env3.level;
    case SRC_ENV4: return x->env4.level;
    case SRC_RANDOM: return x->random;
    case SRC_NOTE: return (x->pitch - 60.0f) / 60.0f;
    case SRC_SEQ_A: case SRC_SEQ_B: case SRC_SEQ_C: case SRC_SEQ_D: return seq_lane(source - SRC_SEQ_A);
    default: return 0;
    }
}

// Sources that swing both ways; the others run 0..1
static int bipolar(int source)
{
    return source == SRC_LFO || source == SRC_LFO2 || source == SRC_RANDOM || source == SRC_NOTE || source >= SRC_SEQ_A;
}

// Adds every route of `layer` evaluated for `voice` into mod[] (DEST_COUNT entries). The VCA is a gain on the amp
// envelope (1 + mod, 0..1): a bipolar source at depth 99 swings it from closed to open (tremolo, gates), and a 0..1
// source counts down from full (source - 1), so velocity → VCA at 99 is plain velocity sensitivity.
static void evaluate_routes(int layer, int voice, float *mod)
{
    for (int r = 0; r < ROUTES; r++) {
        const route_t *x = &routes[layer][r];
        if (x->source == SRC_NONE || x->dest >= DEST_COUNT)
            continue;
        float value = source_value(&voices[voice], voice, layer, x->source);
        if (x->dest == DEST_VCA && !bipolar(x->source))
            value -= 1.0f;
        float depth = (float)x->depth / 99.0f;
        if (x->dest <= DEST_PITCH) // pitch: a squared depth curve, fine near zero for vibrato
            depth *= depth < 0 ? -depth : depth;
        mod[x->dest] += value * depth;
    }
}

static float clamp01(float x) { return x < 0 ? 0 : x > 1 ? 1 : x; }

typedef struct {
    float coarse_a, coarse_b, fine_b, tune, glide_k, env_amount, pmod_env, cutoff, depth, depth2;
} layer_params_t;

static env_rates_t rates[LAYERS][4]; // from the last synth_update(): amp, filter, Env 3, Env 4

// Voice v's envelope-driven CVs from its envelopes now: the VCA, and the cutoff when `cutoff`
static void envelope_cvs(int v, int cutoff)
{
    const voice_t *x = &voices[v];
    if (cutoff)
        sh_set(CV_FIL1 + v, tuning_note_cv(TUNING_ROW_FILTER + v, x->cutoff_rest + x->cutoff_env * x->filter.level));
    float gain = x->amp.level * x->vca_mod;
    sh_set(CV_AMP1 + v, gain > 0 ? cv_from(VCA_THRESHOLD + (1.0f - VCA_THRESHOLD) * gain) : 0);
}

void synth_envelopes(float dt, int cutoff)
{
    if (dt > 0.05f)
        dt = 0.05f;
    for (int v = 0; v < VOICE_COUNT; v++) {
        voice_t *x = &voices[v];
        int l = layer_of(v);
        env_update(&x->amp, dt, &rates[l][0]);
        env_update(&x->filter, dt, &rates[l][1]);
        envelope_cvs(v, cutoff);
    }
}

unsigned synth_active_voices(void)
{
    unsigned mask = 0;
    for (int v = 0; v < VOICE_COUNT; v++)
        if (voices[v].amp.stage != ENV_IDLE)
            mask |= 1u << v;
    return mask;
}

unsigned synth_take_fresh_voices(void)
{
    unsigned mask = 0;
    for (int v = 0; v < VOICE_COUNT; v++)
        if (voices[v].fresh) {
            voices[v].fresh = 0;
            mask |= 1u << v;
        }
    return mask;
}

void synth_update(float dt)
{
    if (dt > 0.05f)
        dt = 0.05f; // after a long stall (tuning, flash writes), don't jump
    read_panel(dt);

    int pmod_freq_a = sw(SWITCH_PMOD_FREQ_A);
    int lfo_to_pitch = sw(SWITCH_LFO_FREQ), lfo_to_filter = sw(SWITCH_LFO_FILTER);

    // routes evaluated for each layer's most recently played voice: the single-wire destinations, and the rate of
    // each LFO's shared phase
    float last_mod[LAYERS][DEST_COUNT] = {{0}};
    for (int l = 0; l < LAYERS; l++)
        if (last_voice[l] >= 0 && (layer_mode != LAYER_OFF || l == 0))
            evaluate_routes(l, last_voice[l], last_mod[l]);

    // the knob values as heard: morphed towards the morph target by the Morph destination
    static float eff[LAYERS][PARAM_COUNT];
    for (int l = 0; l < LAYERS; l++) {
        float m = morph_loaded ? clamp01(last_mod[l][DEST_MORPH]) : 0;
        for (int i = 0; i < PARAM_COUNT; i++)
            eff[l][i] = m > 0 && i != POT_MASTER_VOL ? layer_param[l][i] + (morph_param[l][i] - layer_param[l][i]) * m
                                                     : layer_param[l][i];
    }

    layer_params_t lp[LAYERS];
    for (int l = 0; l < LAYERS; l++) {
        const float *p = eff[l];
        int c = env_cycle[l];
        for (int e = 0; e < 4; e++)
            env_rates(&rates[l][e], p, env_params[e], c >> e & 1, env_sync[l][e]);
        // LFOs (lfo.c): rate or clock division from the rate knobs; LFO_ID(0, 0) goes first
        lfo_update(LFO_ID(l, 0), dt, p[POT_LFO_FREQ], last_mod[l][DEST_LFO_RATE] * MOD_LFO_OCTAVES);
        lfo_update(LFO_ID(l, 1), dt, p[PARAM_LFO2_RATE], last_mod[l][DEST_LFO2_RATE] * MOD_LFO_OCTAVES);
        float glide = synth_knob_glide_s(p[POT_GLIDE]); // time constant, s
        lp[l] = (layer_params_t){
            .coarse_a = semitone_steps(p[POT_FREQ_A]),
            .coarse_b = semitone_steps(p[POT_FREQ_B]),
            .fine_b = (p[POT_FINE] - 0.5f) * 2 * FINE_SEMITONES,
            .tune = (p[POT_MASTER_TUNE] - 0.5f) * 2 * MASTER_TUNE_SEMITONES,
            .glide_k = glide > 0 ? 1.0f - fast_exp2(-dt / glide * 1.4427f) : 1.0f,
            .env_amount = env_amount_of(p[POT_FIL_ENV_AMT]), // -1..1
            .pmod_env = pmod_freq_a ? p[POT_PMOD_FIL_ENV] * p[POT_PMOD_FIL_ENV] : 0,
            .cutoff = CUTOFF_LOW_NOTE + p[POT_CUTOFF] * CUTOFF_SPAN,
            .depth = p[POT_LFO_AMOUNT] * p[POT_LFO_AMOUNT] + mod_wheel,
            .depth2 = p[PARAM_LFO2_AMOUNT] * p[PARAM_LFO2_AMOUNT],
        };
    }

    // shared CVs and gates (LFO → PW from layer 1's LFOs)
    float shared_mod[DEST_COUNT];
    for (int d = 0; d < DEST_COUNT; d++)
        shared_mod[d] = last_mod[0][d] + last_mod[1][d];
    const float *shared = eff[0];
    float pw_depth = clamp01(clamp01(lp[0].depth) * sw(SWITCH_LFO_PW) + shared_mod[DEST_LFO_TO_PW]);
    float pw2_depth = clamp01(lp[0].depth2 * (float)((lfo2_dests[0] & LFO2_TO_PW) != 0) + shared_mod[DEST_LFO2_TO_PW]);
    float lfo_pw = (lfo_value(LFO_ID(0, 0), -1, (lfo_shape_t)lfo_shape[0][0]) * pw_depth
                    + lfo_value(LFO_ID(0, 1), -1, (lfo_shape_t)lfo_shape[0][1]) * pw2_depth) * LFO_PW_DEPTH;
    sh_set(CV_VOL_A, cv_from(shared[POT_A_LEVEL] + shared_mod[DEST_A_LEVEL]));
    sh_set(CV_VOL_B, cv_from(shared[POT_B_LEVEL] + shared_mod[DEST_B_LEVEL]));
    sh_set(CV_NOISE, cv_from(shared[POT_NOISE] + shared_mod[DEST_NOISE]));
    sh_set(CV_RESONANCE, cv_from(shared[POT_RESONANCE] + shared_mod[DEST_RESONANCE]));
    sh_set(CV_PMOD_OSC_B, cv_from(shared[POT_PMOD_OSC_B] + shared_mod[DEST_PMOD_OSC_B]));
    sh_set(CV_MASTER_VOL, cv_from(panel[POT_MASTER_VOL]));
    sh_set(CV_EXT_FIL, 0);
    // square off: PW A at maximum, PW B at minimum (as p600fw), so no pulse bleeds through
    float pw_a = 0.05f + 0.9f * clamp01(shared[POT_A_PW] + shared_mod[DEST_A_PW] * MOD_PW) + lfo_pw;
    float pw_b = 0.05f + 0.9f * clamp01(shared[POT_B_PW] + shared_mod[DEST_B_PW] * MOD_PW) + lfo_pw;
    sh_set(CV_PW_A, sw(SWITCH_A_SQUARE) ? cv_from(pw_a) : 0xFFFF);
    sh_set(CV_PW_B, sw(SWITCH_B_SQUARE) ? cv_from(pw_b) : 0);
    sh_gate(GATE_SAW_A, sw(SWITCH_A_SAW));
    sh_gate(GATE_TRI_A, sw(SWITCH_A_TRI));
    sh_gate(GATE_SAW_B, sw(SWITCH_B_SAW));
    sh_gate(GATE_TRI_B, sw(SWITCH_B_TRI));
    sh_gate(GATE_SYNC, sw(SWITCH_SYNC));
    sh_gate(GATE_PMOD_FREQ_A, pmod_freq_a);
    sh_gate(GATE_PMOD_FILTER, sw(SWITCH_PMOD_FILTER));

    float keytrack_amount = 0.5f * (float)keytrack_setting; // 0, 0.5, 1
    for (int v = 0; v < VOICE_COUNT; v++) {
        voice_t *x = &voices[v];
        int l = layer_of(v);
        const layer_params_t *q = &lp[l];
        env_update(&x->env3, dt, &rates[l][2]); // amp and filter: synth_envelopes()
        env_update(&x->env4, dt, &rates[l][3]);
        x->pitch += ((float)x->note - x->pitch) * q->glide_k;
        if (drift_amount) { // a slow random walk: a new target every 0.4-2 s, approached smoothly
            x->drift_timer -= dt;
            if (x->drift_timer <= 0) {
                x->drift_to[0] = random_signed();
                x->drift_to[1] = random_signed();
                x->drift_timer = 1.2f + 0.8f * random_signed();
            }
            for (int k = 0; k < 2; k++)
                x->drift[k] += (x->drift_to[k] - x->drift[k]) * (dt > 0.5f ? 1.0f : dt * 2.0f);
        }
        float drift = (float)drift_amount / 99.0f;

        float mod[DEST_COUNT] = {0};
        evaluate_routes(l, v, mod);
        lfo_update_voice(LFO_ID(l, 0), v, dt, mod[DEST_LFO_RATE] * MOD_LFO_OCTAVES);
        lfo_update_voice(LFO_ID(l, 1), v, dt, mod[DEST_LFO2_RATE] * MOD_LFO_OCTAVES);

        int first, end;
        layer_voices(l, &first, &end);
        int n = end - first;
        float spread = unison && n > 1 ? ((float)(v - first) / (float)(n - 1) - 0.5f) * (float)detune / 100.0f : 0;
        float lfo = lfo_value(LFO_ID(l, 0), v, (lfo_shape_t)lfo_shape[l][0]);
        float lfo2 = lfo_value(LFO_ID(l, 1), v, (lfo_shape_t)lfo_shape[l][1]);
        float depth = clamp01(q->depth + mod[DEST_LFO_AMOUNT]);
        float depth2 = clamp01(q->depth2 + mod[DEST_LFO2_AMOUNT]);
        float to_pitch = clamp01(depth * lfo_to_pitch + mod[DEST_LFO_TO_PITCH]) * LFO_PITCH_SEMITONES;
        float to_filter = clamp01(depth * lfo_to_filter + mod[DEST_LFO_TO_FILTER]) * LFO_FILTER_SEMITONES;
        float to_pitch2 = clamp01(depth2 * (float)((lfo2_dests[l] & LFO2_TO_PITCH) != 0) + mod[DEST_LFO2_TO_PITCH])
                        * LFO_PITCH_SEMITONES;
        float to_filter2 = clamp01(depth2 * (float)((lfo2_dests[l] & LFO2_TO_FILTER) != 0)
                                   + mod[DEST_LFO2_TO_FILTER]) * LFO_FILTER_SEMITONES;
        float env_amount = (q->env_amount + mod[DEST_FIL_ENV_AMT]) * FILTER_ENV_SEMITONES;
        float pmod_env = (q->pmod_env + mod[DEST_PMOD_ENV]) * PMOD_ENV_SEMITONES;

        float pitch_mod = lfo * to_pitch + lfo2 * to_pitch2 + mod[DEST_PITCH] * MOD_PITCH_SEMITONES;
        if (scale) // snap the note with its modulation to the scale
            pitch_mod = quantize((float)x->note + pitch_mod) - (float)x->note;
        float pitch = x->pitch + bend * (float)bend_range + q->tune + spread + pitch_mod
                    + x->drift[0] * drift * DRIFT_PITCH_SEMITONES;
        float a = pitch + q->coarse_a + pmod_env * x->filter.level + mod[DEST_PITCH_A] * MOD_PITCH_SEMITONES;
        float b = pitch + q->coarse_b + q->fine_b + mod[DEST_PITCH_B] * MOD_PITCH_SEMITONES;
        x->cutoff_rest = q->cutoff + keytrack_amount * (x->pitch - 60.0f) + lfo * to_filter + lfo2 * to_filter2
                       + mod[DEST_CUTOFF] * MOD_CUTOFF_SEMITONES + x->drift[1] * drift * DRIFT_CUTOFF_SEMITONES;
        x->cutoff_env = env_amount;
        x->vca_mod = clamp01(1.0f + mod[DEST_VCA]);

        sh_set(CV_OSC_A1 + v, tuning_note_cv(TUNING_ROW_OSC_A + v, a));
        sh_set(CV_OSC_B1 + v, tuning_note_cv(TUNING_ROW_OSC_B + v, b));
        envelope_cvs(v, 1);
    }
}
