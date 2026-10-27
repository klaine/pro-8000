// Layout follows the Prophet-600 panel the PRO-800 copies (and p600fw's behaviour where it's documented):
//   oscillator A: frequency (semitone steps), saw / triangle / square with pulse width, sync to B
//   oscillator B: frequency (semitone steps) and fine, saw / triangle / square with pulse width
//   mixer: A, B and noise levels; filter: cutoff, resonance, envelope amount (bipolar), keyboard tracking
//   poly-mod: filter envelope (software, added to oscillator A's pitch) and oscillator B (analog), routed to
//   frequency A and/or the filter by the two poly-mod buttons
//   LFO (lfo.c): triangle or square, to frequency, pulse width and/or filter, free or synced to MIDI clock, with
//   retrigger modes; glide, unison, master tune and volume
//
// Layers: two sets of the knobs that act per voice (frequencies, fine, cutoff, envelope amount, both ADSRs,
// glide, LFO rate and amount, poly-mod envelope amount, master tune) and an LFO each. Layer 1 plays on voices 1-4
// and layer 2 on voices 5-8, except with layers off, when layer 1 has all 8. Everything else is one set of
// wires for all voices on the hardware, so it's shared: mixer, resonance, pulse widths, poly-mod osc B amount
// and routing, master volume, every button, and LFO → PW (from layer 1's LFO). After switching the edit layer,
// a per-layer knob waits until it's turned past (or onto) that layer's value before it takes over. Loading a
// patch puts every stored knob (all but M VOL) in pickup the same way.
//
// Ranges and curves are first guesses, chosen to be playable; they're constants here.
#include "synth.h"
#include "controls.h"
#include "fastmath.h"
#include "lfo.h"
#include "sh.h"
#include "tuning.h"

#define OSC_RANGE_SEMITONES   24.0f // frequency knobs: ± this, in semitone steps
#define FINE_SEMITONES        1.0f  // oscillator B fine: ±
#define MASTER_TUNE_SEMITONES 1.0f  // ±
#define BEND_SEMITONES        2.0f  // ±
#define CUTOFF_LOW_NOTE       12.0f // cutoff knob at minimum (about 16 Hz)
#define CUTOFF_SPAN           120.0f
#define FILTER_ENV_SEMITONES  72.0f // envelope amount at either end
#define PMOD_ENV_SEMITONES    48.0f // poly-mod filter envelope → frequency A, at maximum
#define LFO_PITCH_SEMITONES   12.0f
#define LFO_FILTER_SEMITONES  48.0f
#define LFO_PW_DEPTH          0.45f
#define UNISON_SPREAD         0.02f // semitones between neighbouring voices
#define POT_SMOOTHING_S       0.01f
#define PICKUP_WINDOW         0.015f // a knob this close to the stored value picks it up

#define LAYERS 2

typedef enum { ENV_IDLE, ENV_ATTACK, ENV_DECAY, ENV_RELEASE } env_stage_t;

typedef struct {
    env_stage_t stage;
    float level; // 0..1
} env_t;

typedef struct {
    uint8_t note;
    uint8_t gate;
    uint8_t velocity;
    uint32_t started; // allocation order
    float pitch;      // gliding note, in semitones (MIDI note numbers)
    env_t amp, filter;
} voice_t;

static voice_t voices[VOICE_COUNT];
static uint32_t allocations;
static float bend, mod_wheel;

// the panel's pots, smoothed (0..1), and each layer's values
static float panel[POT_COUNT];
static float layer_pot[LAYERS][POT_COUNT];
static int pots_primed;
static int edit_layer;
static uint8_t latched[POT_COUNT]; // the knob controls its value (per-layer knobs: the edit layer's)
static int8_t pickup_side[POT_COUNT]; // which side of the stored value the knob started on: -1, +1 (0 = unknown)

static const uint8_t per_layer[POT_COUNT] = {
    [POT_FREQ_A] = 1, [POT_FREQ_B] = 1, [POT_FINE] = 1, [POT_CUTOFF] = 1, [POT_FIL_ENV_AMT] = 1,
    [POT_FIL_ATT] = 1, [POT_FIL_DEC] = 1, [POT_FIL_SUS] = 1, [POT_FIL_REL] = 1,
    [POT_AMP_ATT] = 1, [POT_AMP_DEC] = 1, [POT_AMP_SUS] = 1, [POT_AMP_REL] = 1,
    [POT_GLIDE] = 1, [POT_LFO_FREQ] = 1, [POT_LFO_AMOUNT] = 1, [POT_PMOD_FIL_ENV] = 1, [POT_MASTER_TUNE] = 1,
};

// buttons: a loaded patch's state holds until the button is toggled
static uint8_t switch_override[SWITCH_COUNT], switch_value[SWITCH_COUNT], switch_at_load[SWITCH_COUNT];
static uint8_t keytrack_override, keytrack_value, keytrack_at_load;

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

void synth_set_edit_layer(int layer)
{
    if (layer == edit_layer)
        return;
    edit_layer = layer;
    for (int i = 0; i < POT_COUNT; i++)
        if (per_layer[i]) {
            latched[i] = 0;
            pickup_side[i] = 0;
        }
}

float synth_pot_value(int pot) { return layer_pot[edit_layer][pot]; }

int synth_pot_pending(int pot) { return !latched[pot]; }

static int sw(switch_t s)
{
    int physical = controls_on(s);
    if (switch_override[s] && physical != switch_at_load[s])
        switch_override[s] = 0; // toggled since the patch was loaded
    return switch_override[s] ? switch_value[s] : physical;
}

static int keytrack(void)
{
    int physical = controls_keytrack();
    if (keytrack_override && physical != keytrack_at_load)
        keytrack_override = 0;
    return keytrack_override ? keytrack_value : physical;
}

void synth_apply_patch(const patch_t *patch)
{
    synth_all_off();
    for (int l = 0; l < LAYERS; l++)
        for (int i = 0; i < POT_COUNT; i++)
            if (i != POT_MASTER_VOL)
                layer_pot[l][i] = (float)patch->pots[per_layer[i] ? l : 0][i] / 65535.0f;
    for (int i = 0; i < POT_COUNT; i++) {
        latched[i] = i == POT_MASTER_VOL;
        pickup_side[i] = 0;
    }
    for (int s = 0; s < SWITCH_COUNT; s++) {
        switch_value[s] = (patch->switches >> s) & 1;
        switch_at_load[s] = (uint8_t)controls_on((switch_t)s);
        switch_override[s] = s <= SWITCH_UNISON || s == SWITCH_SYNC;
    }
    keytrack_value = patch->keytrack <= 2 ? patch->keytrack : 0;
    keytrack_at_load = (uint8_t)controls_keytrack();
    keytrack_override = 1;
    layer_mode = patch->layer_mode < LAYER_MODE_COUNT ? (layer_mode_t)patch->layer_mode : LAYER_OFF;
    synth_set_split_note(patch->split);
    lfo_sync = patch->lfo_sync < LFO_SYNC_COUNT ? (lfo_sync_t)patch->lfo_sync : LFO_SYNC_FREE;
    lfo_trigger = patch->lfo_trigger < LFO_TRIGGER_COUNT ? (lfo_trigger_t)patch->lfo_trigger : LFO_TRIGGER_FREE;
    unison = sw(SWITCH_UNISON);
}

void synth_capture_patch(patch_t *patch)
{
    for (int l = 0; l < LAYERS; l++)
        for (int i = 0; i < POT_COUNT; i++) {
            float v = layer_pot[per_layer[i] ? l : 0][i];
            patch->pots[l][i] = (uint16_t)(v <= 0 ? 0 : v >= 1 ? 65535 : v * 65535.0f + 0.5f);
        }
    patch->switches = 0;
    for (int s = 0; s < SWITCH_COUNT; s++)
        if ((s <= SWITCH_UNISON || s == SWITCH_SYNC) && sw((switch_t)s)) // the latching buttons only
            patch->switches |= 1u << s;
    patch->keytrack = (uint8_t)keytrack();
    patch->layer_mode = (uint8_t)layer_mode;
    patch->split = (uint8_t)split_note;
    patch->lfo_sync = (uint8_t)lfo_sync;
    patch->lfo_trigger = (uint8_t)lfo_trigger;
}

void synth_manual(void)
{
    for (int i = 0; i < POT_COUNT; i++)
        latched[i] = 1;
    for (int s = 0; s < SWITCH_COUNT; s++)
        switch_override[s] = 0;
    keytrack_override = 0;
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

static void gate_on(voice_t *x, uint8_t note, uint8_t velocity, int retrigger)
{
    int was_silent = x->amp.stage == ENV_IDLE;
    x->note = note;
    x->gate = 1;
    x->velocity = velocity;
    x->started = ++allocations;
    if (was_silent)
        x->pitch = note; // glide only from a sounding note
    if (retrigger) {
        x->amp.stage = ENV_ATTACK; // from the current level, so a stolen voice doesn't click
        x->filter.stage = ENV_ATTACK;
    }
}

static void gate_off(voice_t *x)
{
    x->gate = 0;
    if (x->amp.stage != ENV_IDLE)
        x->amp.stage = ENV_RELEASE;
    if (x->filter.stage != ENV_IDLE)
        x->filter.stage = ENV_RELEASE;
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

static void layer_note_on(int layer, uint8_t note, uint8_t velocity)
{
    int first, end;
    layer_voices(layer, &first, &end);
    if (unison) {
        int was_held = held_count[layer] > 0;
        remove_held(layer, note); // moves to the top if already held
        if (held_count[layer] == HELD_MAX)
            remove_held(layer, held[layer][0]);
        held[layer][held_count[layer]++] = note;
        for (int v = first; v < end; v++) {
            gate_on(&voices[v], note, velocity, !was_held); // legato while a note is already held
            lfo_note_on(layer, v, !was_held);
        }
        return;
    }
    int was_gated = layer_gated(layer);
    int v = allocate(layer, note);
    gate_on(&voices[v], note, velocity, 1);
    lfo_note_on(layer, v, !was_gated);
}

void synth_note_on(uint8_t note, uint8_t velocity, int channel)
{
    switch (layer_mode) {
    case LAYER_OFF:
        layer_note_on(0, note, velocity);
        break;
    case LAYER_SPLIT:
        layer_note_on(note < split_note ? 0 : 1, note, velocity);
        break;
    case LAYER_STACK:
        layer_note_on(0, note, velocity);
        layer_note_on(1, note, velocity);
        break;
    case LAYER_CHANNEL:
        layer_note_on(channel == 1 ? 1 : 0, note, velocity);
        break;
    default:
        break;
    }
}

void synth_note_off(uint8_t note)
{
    for (int layer = 0; layer < LAYERS; layer++) {
        int first, end;
        layer_voices(layer, &first, &end);
        if (unison) {
            int was_held = held_count[layer] > 0;
            remove_held(layer, note);
            if (!was_held)
                continue;
            for (int v = first; v < end; v++) {
                if (held_count[layer])
                    voices[v].note = held[layer][held_count[layer] - 1]; // back to the previous note, legato
                else
                    gate_off(&voices[v]);
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

void synth_pitch_bend(int value) { bend = (float)(value - 8192) / 8192.0f * BEND_SEMITONES; }

void synth_mod_wheel(uint8_t value) { mod_wheel = (float)value / 127.0f; }

int synth_any_gate(void)
{
    for (int v = 0; v < VOICE_COUNT; v++)
        if (voices[v].gate)
            return 1;
    return 0;
}

// --- modulation ---

// 1 ms .. 10 s, exponentially over the knob
static float env_time(float knob) { return 0.001f * fast_exp2(knob * 13.2877f); }

static float decay_factor(float dt, float time) // reaches about 2% of the way in `time`
{
    return fast_exp2(-dt / time * 5.64f);
}

static void env_update(env_t *e, float dt, float attack, float decay, float sustain, float release)
{
    switch (e->stage) {
    case ENV_IDLE:
        e->level = 0;
        break;
    case ENV_ATTACK:
        e->level += dt / env_time(attack);
        if (e->level >= 1.0f) {
            e->level = 1.0f;
            e->stage = ENV_DECAY;
        }
        break;
    case ENV_DECAY: // then follows the sustain knob while held
        e->level = sustain + (e->level - sustain) * decay_factor(dt, env_time(decay));
        break;
    case ENV_RELEASE:
        e->level *= decay_factor(dt, env_time(release));
        if (e->level < 0.0005f) {
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

static void read_panel(float dt)
{
    float k = dt >= POT_SMOOTHING_S || !pots_primed ? 1.0f : dt / POT_SMOOTHING_S;
    for (int i = 0; i < POT_COUNT; i++) {
        panel[i] += ((float)controls_pot(i) / 4095.0f - panel[i]) * k;
        if (!pots_primed) { // both layers start from the panel
            layer_pot[0][i] = layer_pot[1][i] = panel[i];
            latched[i] = 1;
            continue;
        }
        int layer = per_layer[i] ? edit_layer : 0; // shared knobs keep one value, in both layers
        if (!latched[i]) {
            float diff = panel[i] - layer_pot[layer][i];
            int8_t side = diff < 0 ? -1 : 1;
            if (diff < PICKUP_WINDOW && diff > -PICKUP_WINDOW)
                latched[i] = 1;
            else if (pickup_side[i] == 0)
                pickup_side[i] = side;
            else if (side != pickup_side[i])
                latched[i] = 1; // swept past it
        }
        if (latched[i]) {
            if (per_layer[i])
                layer_pot[edit_layer][i] = panel[i];
            else
                layer_pot[0][i] = layer_pot[1][i] = panel[i];
        }
    }
    pots_primed = controls_ready(); // until the first full scan, keep copying the panel into both layers

    int now_unison = sw(SWITCH_UNISON);
    if (now_unison != unison) {
        synth_all_off();
        unison = now_unison;
    }
}

typedef struct {
    float coarse_a, coarse_b, fine_b, tune, glide_k, env_amount, pmod_env, cutoff, to_pitch, to_filter;
} layer_params_t;

void synth_update(float dt)
{
    if (dt > 0.05f)
        dt = 0.05f; // after a long stall (tuning, flash writes), don't jump
    read_panel(dt);

    int square = sw(SWITCH_LFO_SHAPE);
    int pmod_freq_a = sw(SWITCH_PMOD_FREQ_A);
    layer_params_t lp[LAYERS];
    for (int l = 0; l < LAYERS; l++) {
        const float *p = layer_pot[l];
        // LFO (lfo.c): rate or clock division from this layer's LFO FREQ
        lfo_update(l, dt, p[POT_LFO_FREQ]);
        float depth = p[POT_LFO_AMOUNT] * p[POT_LFO_AMOUNT] + mod_wheel;
        if (depth > 1)
            depth = 1;
        float glide = p[POT_GLIDE] < 0.02f ? 0 : 0.002f * fast_exp2(p[POT_GLIDE] * 10.0f); // time constant, s
        lp[l] = (layer_params_t){
            .coarse_a = semitone_steps(p[POT_FREQ_A]),
            .coarse_b = semitone_steps(p[POT_FREQ_B]),
            .fine_b = (p[POT_FINE] - 0.5f) * 2 * FINE_SEMITONES,
            .tune = (p[POT_MASTER_TUNE] - 0.5f) * 2 * MASTER_TUNE_SEMITONES,
            .glide_k = glide > 0 ? 1.0f - fast_exp2(-dt / glide * 1.4427f) : 1.0f,
            .env_amount = (p[POT_FIL_ENV_AMT] - 0.5f) * 2 * FILTER_ENV_SEMITONES,
            .pmod_env = pmod_freq_a ? p[POT_PMOD_FIL_ENV] * PMOD_ENV_SEMITONES : 0,
            .cutoff = CUTOFF_LOW_NOTE + p[POT_CUTOFF] * CUTOFF_SPAN,
            .to_pitch = sw(SWITCH_LFO_FREQ) ? depth * LFO_PITCH_SEMITONES : 0,
            .to_filter = sw(SWITCH_LFO_FILTER) ? depth * LFO_FILTER_SEMITONES : 0,
        };
    }

    // shared CVs and gates
    float depth0 = layer_pot[0][POT_LFO_AMOUNT] * layer_pot[0][POT_LFO_AMOUNT] + mod_wheel;
    float lfo_pw = sw(SWITCH_LFO_PW) ? lfo_value(0, -1, square) * (depth0 > 1 ? 1 : depth0) * LFO_PW_DEPTH : 0;
    const float *shared = layer_pot[0];
    sh_set(CV_VOL_A, cv_from(shared[POT_A_LEVEL]));
    sh_set(CV_VOL_B, cv_from(shared[POT_B_LEVEL]));
    sh_set(CV_NOISE, cv_from(shared[POT_NOISE]));
    sh_set(CV_RESONANCE, cv_from(shared[POT_RESONANCE]));
    sh_set(CV_PMOD_OSC_B, cv_from(shared[POT_PMOD_OSC_B]));
    sh_set(CV_MASTER_VOL, cv_from(panel[POT_MASTER_VOL]));
    sh_set(CV_EXT_FIL, 0);
    // square off: PW A at maximum, PW B at minimum (as p600fw), so no pulse bleeds through
    sh_set(CV_PW_A, sw(SWITCH_A_SQUARE) ? cv_from(0.05f + 0.9f * shared[POT_A_PW] + lfo_pw) : 0xFFFF);
    sh_set(CV_PW_B, sw(SWITCH_B_SQUARE) ? cv_from(0.05f + 0.9f * shared[POT_B_PW] + lfo_pw) : 0);
    sh_gate(GATE_SAW_A, sw(SWITCH_A_SAW));
    sh_gate(GATE_TRI_A, sw(SWITCH_A_TRI));
    sh_gate(GATE_SAW_B, sw(SWITCH_B_SAW));
    sh_gate(GATE_TRI_B, sw(SWITCH_B_TRI));
    sh_gate(GATE_SYNC, sw(SWITCH_SYNC));
    sh_gate(GATE_PMOD_FREQ_A, pmod_freq_a);
    sh_gate(GATE_PMOD_FILTER, sw(SWITCH_PMOD_FILTER));

    float keytrack_amount = 0.5f * (float)keytrack(); // 0, 0.5, 1
    for (int v = 0; v < VOICE_COUNT; v++) {
        voice_t *x = &voices[v];
        int l = layer_of(v);
        const float *p = layer_pot[l];
        const layer_params_t *q = &lp[l];
        env_update(&x->amp, dt, p[POT_AMP_ATT], p[POT_AMP_DEC], p[POT_AMP_SUS], p[POT_AMP_REL]);
        env_update(&x->filter, dt, p[POT_FIL_ATT], p[POT_FIL_DEC], p[POT_FIL_SUS], p[POT_FIL_REL]);
        x->pitch += ((float)x->note - x->pitch) * q->glide_k;

        int first, end;
        layer_voices(l, &first, &end);
        float spread = unison ? ((float)(v - first) - (float)(end - first - 1) / 2) * UNISON_SPREAD : 0;
        float lfo = lfo_value(l, v, square);
        float pitch = x->pitch + bend + q->tune + spread + lfo * q->to_pitch;
        float a = pitch + q->coarse_a + q->pmod_env * x->filter.level;
        float b = pitch + q->coarse_b + q->fine_b;
        float filter = q->cutoff + keytrack_amount * (x->pitch - 60.0f) + q->env_amount * x->filter.level + lfo * q->to_filter;

        sh_set(CV_OSC_A1 + v, tuning_note_cv(TUNING_ROW_OSC_A + v, a));
        sh_set(CV_OSC_B1 + v, tuning_note_cv(TUNING_ROW_OSC_B + v, b));
        sh_set(CV_FIL1 + v, tuning_note_cv(TUNING_ROW_FILTER + v, filter));
        sh_set(CV_AMP1 + v, cv_from(x->amp.level));
    }
}
