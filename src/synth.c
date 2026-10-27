// Layout follows the Prophet-600 panel the PRO-800 copies (and p600fw's behaviour where it's documented):
//   oscillator A: frequency (semitone steps), saw / triangle / square with pulse width, sync to B
//   oscillator B: frequency (semitone steps) and fine, saw / triangle / square with pulse width
//   mixer: A, B and noise levels; filter: cutoff, resonance, envelope amount (bipolar), keyboard tracking
//   poly-mod: filter envelope (software, added to oscillator A's pitch) and oscillator B (analog), routed to
//   frequency A and/or the filter by the two poly-mod buttons
//   LFO (lfo.c): triangle or square, to frequency, pulse width and/or filter, free or synced to MIDI clock, with
//   retrigger modes; glide, unison, master tune and volume
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

typedef enum { ENV_IDLE, ENV_ATTACK, ENV_DECAY, ENV_SUSTAIN, ENV_RELEASE } env_stage_t;

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
static float pot[POT_COUNT]; // smoothed, 0..1
static float bend, mod_wheel;
static int pots_primed;

// unison: held notes, last one sounds
#define HELD_MAX 16
static uint8_t held[HELD_MAX];
static int held_count;
static int unison;

void synth_init(void)
{
    for (int v = 0; v < VOICE_COUNT; v++) {
        voices[v] = (voice_t){0};
        voices[v].pitch = 60;
    }
    held_count = 0;
}

// --- notes ---

// Prefers the voice already on this note, then a silent one, then the oldest released, then the oldest.
static int allocate(uint8_t note)
{
    int best = -1;
    for (int v = 0; v < VOICE_COUNT; v++)
        if (voices[v].note == note && (voices[v].gate || voices[v].amp.stage != ENV_IDLE))
            return v;
    for (int pass = 0; pass < 3 && best < 0; pass++)
        for (int v = 0; v < VOICE_COUNT; v++) {
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

void synth_note_on(uint8_t note, uint8_t velocity)
{
    if (unison) {
        int was_held = held_count > 0;
        for (int i = 0; i < held_count; i++) // move to the top if already held
            if (held[i] == note) {
                for (int j = i; j < held_count - 1; j++)
                    held[j] = held[j + 1];
                held_count--;
                break;
            }
        if (held_count == HELD_MAX) {
            for (int j = 0; j < HELD_MAX - 1; j++)
                held[j] = held[j + 1];
            held_count--;
        }
        held[held_count++] = note;
        for (int v = 0; v < VOICE_COUNT; v++) {
            gate_on(&voices[v], note, velocity, !was_held); // legato while a note is already held
            lfo_note_on(v, !was_held);
        }
        return;
    }
    int first = !synth_any_gate();
    int v = allocate(note);
    gate_on(&voices[v], note, velocity, 1);
    lfo_note_on(v, first);
}

void synth_note_off(uint8_t note)
{
    if (unison) {
        for (int i = 0; i < held_count; i++)
            if (held[i] == note) {
                for (int j = i; j < held_count - 1; j++)
                    held[j] = held[j + 1];
                held_count--;
                break;
            }
        for (int v = 0; v < VOICE_COUNT; v++) {
            if (held_count)
                voices[v].note = held[held_count - 1]; // back to the previous note, legato
            else
                gate_off(&voices[v]);
        }
        return;
    }
    for (int v = 0; v < VOICE_COUNT; v++)
        if (voices[v].gate && voices[v].note == note)
            gate_off(&voices[v]);
}

void synth_all_off(void)
{
    held_count = 0;
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
    case ENV_DECAY:
    case ENV_SUSTAIN: // follows the sustain knob while held
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

static void read_panel(float dt)
{
    float k = dt >= POT_SMOOTHING_S || !pots_primed ? 1.0f : dt / POT_SMOOTHING_S;
    for (int i = 0; i < POT_COUNT; i++)
        pot[i] += ((float)controls_pot(i) / 4095.0f - pot[i]) * k;
    pots_primed = 1;

    int now_unison = controls_on(SWITCH_UNISON);
    if (now_unison != unison) {
        synth_all_off();
        unison = now_unison;
    }
}

void synth_update(float dt)
{
    if (dt > 0.05f)
        dt = 0.05f; // after a long stall (tuning, flash writes), don't jump
    read_panel(dt);

    // LFO (lfo.c): rate or clock division from LFO FREQ; triangle, or square with LFO Shape on
    lfo_update(dt, pot[POT_LFO_FREQ]);
    int square = controls_on(SWITCH_LFO_SHAPE);
    float depth = pot[POT_LFO_AMOUNT] * pot[POT_LFO_AMOUNT] + mod_wheel;
    if (depth > 1)
        depth = 1;
    float to_pitch = controls_on(SWITCH_LFO_FREQ) ? depth * LFO_PITCH_SEMITONES : 0;
    float to_filter = controls_on(SWITCH_LFO_FILTER) ? depth * LFO_FILTER_SEMITONES : 0;
    float lfo_pw = controls_on(SWITCH_LFO_PW) ? lfo_value(-1, square) * depth * LFO_PW_DEPTH : 0; // one PW CV

    // global CVs and gates
    sh_set(CV_VOL_A, cv_from(pot[POT_A_LEVEL]));
    sh_set(CV_VOL_B, cv_from(pot[POT_B_LEVEL]));
    sh_set(CV_NOISE, cv_from(pot[POT_NOISE]));
    sh_set(CV_RESONANCE, cv_from(pot[POT_RESONANCE]));
    sh_set(CV_PMOD_OSC_B, cv_from(pot[POT_PMOD_OSC_B]));
    sh_set(CV_MASTER_VOL, cv_from(pot[POT_MASTER_VOL]));
    sh_set(CV_EXT_FIL, 0);
    // square off: PW A at maximum, PW B at minimum (as p600fw), so no pulse bleeds through
    sh_set(CV_PW_A, controls_on(SWITCH_A_SQUARE) ? cv_from(0.05f + 0.9f * pot[POT_A_PW] + lfo_pw) : 0xFFFF);
    sh_set(CV_PW_B, controls_on(SWITCH_B_SQUARE) ? cv_from(0.05f + 0.9f * pot[POT_B_PW] + lfo_pw) : 0);
    sh_gate(GATE_SAW_A, controls_on(SWITCH_A_SAW));
    sh_gate(GATE_TRI_A, controls_on(SWITCH_A_TRI));
    sh_gate(GATE_SAW_B, controls_on(SWITCH_B_SAW));
    sh_gate(GATE_TRI_B, controls_on(SWITCH_B_TRI));
    sh_gate(GATE_SYNC, controls_on(SWITCH_SYNC));
    int pmod_freq_a = controls_on(SWITCH_PMOD_FREQ_A);
    sh_gate(GATE_PMOD_FREQ_A, pmod_freq_a);
    sh_gate(GATE_PMOD_FILTER, controls_on(SWITCH_PMOD_FILTER));

    // per-voice
    float coarse_a = (float)(int)((pot[POT_FREQ_A] - 0.5f) * 2 * OSC_RANGE_SEMITONES + OSC_RANGE_SEMITONES + 0.5f)
                   - OSC_RANGE_SEMITONES;
    float coarse_b = (float)(int)((pot[POT_FREQ_B] - 0.5f) * 2 * OSC_RANGE_SEMITONES + OSC_RANGE_SEMITONES + 0.5f)
                   - OSC_RANGE_SEMITONES;
    float fine_b = (pot[POT_FINE] - 0.5f) * 2 * FINE_SEMITONES;
    float common = bend + (pot[POT_MASTER_TUNE] - 0.5f) * 2 * MASTER_TUNE_SEMITONES;
    float glide = pot[POT_GLIDE] < 0.02f ? 0 : 0.002f * fast_exp2(pot[POT_GLIDE] * 10.0f); // time constant, s
    float glide_k = glide > 0 ? 1.0f - fast_exp2(-dt / glide * 1.4427f) : 1.0f;
    float keytrack = 0.5f * (float)controls_keytrack(); // 0, 0.5, 1
    float env_amount = (pot[POT_FIL_ENV_AMT] - 0.5f) * 2 * FILTER_ENV_SEMITONES;
    float pmod_env = pmod_freq_a ? pot[POT_PMOD_FIL_ENV] * PMOD_ENV_SEMITONES : 0;
    float cutoff = CUTOFF_LOW_NOTE + pot[POT_CUTOFF] * CUTOFF_SPAN;

    for (int v = 0; v < VOICE_COUNT; v++) {
        voice_t *x = &voices[v];
        env_update(&x->amp, dt, pot[POT_AMP_ATT], pot[POT_AMP_DEC], pot[POT_AMP_SUS], pot[POT_AMP_REL]);
        env_update(&x->filter, dt, pot[POT_FIL_ATT], pot[POT_FIL_DEC], pot[POT_FIL_SUS], pot[POT_FIL_REL]);
        x->pitch += ((float)x->note - x->pitch) * glide_k;

        float spread = unison ? ((float)v - 3.5f) * UNISON_SPREAD : 0;
        float lfo = lfo_value(v, square);
        float pitch = x->pitch + common + spread + lfo * to_pitch;
        float a = pitch + coarse_a + pmod_env * x->filter.level;
        float b = pitch + coarse_b + fine_b;
        float filter = cutoff + keytrack * (x->pitch - 60.0f) + env_amount * x->filter.level + lfo * to_filter;

        sh_set(CV_OSC_A1 + v, tuning_note_cv(TUNING_ROW_OSC_A + v, a));
        sh_set(CV_OSC_B1 + v, tuning_note_cv(TUNING_ROW_OSC_B + v, b));
        sh_set(CV_FIL1 + v, tuning_note_cv(TUNING_ROW_FILTER + v, filter));
        sh_set(CV_AMP1 + v, cv_from(x->amp.level));
    }
}
