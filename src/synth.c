// Layout follows the Prophet-600 panel the PRO-800 copies (and p600fw's behaviour where it's documented):
//   oscillator A: frequency (semitone steps), saw / triangle / square with pulse width, sync to B
//   oscillator B: frequency (semitone steps) and fine, saw / triangle / square with pulse width
//   mixer: A, B and noise levels; filter: cutoff, resonance, envelope amount (bipolar), keyboard tracking
//   poly-mod: filter envelope (software, added to oscillator A's pitch) and oscillator B (analog), routed to
//   frequency A and/or the filter by the two poly-mod buttons
//   LFO 1 (lfo.c): nine shapes, steps and noise included (the shape button steps through them), to frequency, pulse width and/or filter,
//   free or synced to MIDI clock, with retrigger modes; glide, unison and volume; M TUNE is Macro 1's knob
//   LFO 2: the same, edited with the LFO knobs and buttons while Arp Up-Dn selects it (its routing lives here, the
//   buttons keep showing LFO 1's)
//
// Layers: two sets of the knobs that act per voice (frequencies, fine, cutoff, envelope amount, both ADSRs,
// glide, LFO rates and amounts, poly-mod envelope amount, M TUNE = Macro 1) and two LFOs each, with their own shapes and
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
// for as long as the key is held, or for a count of cycles (then it stays at rest); on key up it falls with the DEC
// time. Hold and rest use the attack's range.
// Synced cycling: the cycle lasts a clock division (lfo.c's tempo) and the four knobs are weights that share it
// out (attack, hold, fall, rest in proportion). A phase drives it, so it stays on tempo however long it runs; it
// restarts at each key.
// Per envelope and layer, besides (0.9.0): the key-up trigger (the envelope runs when the key is let go: attack,
// decay for its time, release; cycling, it cycles from the key up, its count of cycles or until the
// next note on that voice), DAHDSR (a delay before the attack, during which the level keeps falling as in a
// release, and a hold at the top), and the curve (analog, as above; linear; swell, a convex attack). Cycling takes
// precedence over DAHDSR. Linear stages cover full scale in their time (a release from half way takes half the
// time); the swell attack grows 2^5 from just under 0, so it reaches 1 in its time.
// Stage times in note values (per envelope and layer): the time knobs (and the delay and hold) step through 0 and the
// LFO's clock divisions, 1/32 .. 8 bars, at the clock's tempo, instead of seconds. Synced cycling keeps its shares.
//
// M TUNE is Macro 1's knob (0.9.0): bipolar, added to the macro's offset (Shift + CUTOFF) and its routes; master tune
// itself is gone (autotune keeps the voices in tune).
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
#define BEND_DEFAULT          2     // ± semitones, when the patch doesn't set it
#define CUTOFF_LOW_NOTE       12.0f // cutoff knob at minimum (about 16 Hz)
#define CUTOFF_SPAN           120.0f
#define FILTER_ENV_SEMITONES  72.0f // envelope amount at either end (squared: 25 % = 4.5, 50 % = 18 semitones)
#define PMOD_ENV_SEMITONES    48.0f // poly-mod filter envelope → frequency A, at maximum (squared: 50 % = 12)
#define LFO_PITCH_SEMITONES   12.0f
#define LFO_FILTER_SEMITONES  48.0f
#define LFO_PW_DEPTH          0.45f
// PW knob → CV: the pulse is high for 1.149 × CV − 0.0094 of the cycle (measured through the Move, 0.9.0) and gone
// from about CV 0.88 (0.86 on voice 6), so the knob's 0..1 maps to 5..95 % of the cycle, 50 a square
#define PW_CV_BOTTOM          0.0517f
#define PW_CV_RANGE           0.7833f
#define DETUNE_DEFAULT        14    // unison: cents between the outermost voices, when the patch doesn't set it
#define MPE_BEND_SEMITONES    48.0f // an MPE member channel's bend range (the MPE default; RPNs aren't parsed)
#define POT_SMOOTHING_S       0.01f  // CUTOFF and NOISE, read every pass
#define MUX_SCAN_S            0.032f // the other pots, read every 32 ms (controls.c): each reading is glided to
#define PICKUP_WINDOW         0.015f // a knob this close to the stored value picks it up
#define JUMP_MOVE             0.02f  // pickup off: a knob moved this far from where it started takes over
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
#define SWELL_OCTAVES         5.0f  // the swell curve's attack: 2^5 from SWELL_OFFSET below 0 to 1 above it
#define SWELL_OFFSET          (1.0f / 31.0f)
#define MACRO_DEAD_ZONE       0.02f // M TUNE as Macro 1: this far either side of the middle reads 0
// The voice VCAs are linear in amplitude above a dead zone: measured 2026-10-27, silent up to 7.3-9.0 % of the CV
// range depending on the voice (8.9 % on voice 1, fit from -37 dB to full). The envelope starts just under the
// lowest, so tails fade instead of stopping at about -20 dB; an idle voice still gets 0.
#define VCA_THRESHOLD         0.07f
#define ENV_FLOOR             0.0005f // -66 dB: a release ends here
#define CYCLE_FLOOR           0.001f  // -60 dB: a cycle's fall ends here, on time

#define DRIFT_PITCH_SEMITONES 0.15f // at a drift of 99
#define DRIFT_CUTOFF_SEMITONES 1.0f

#define LAYERS 2

typedef enum { ENV_IDLE, ENV_ATTACK, ENV_DECAY, ENV_RELEASE, ENV_HOLD, ENV_REST, ENV_DELAY } env_stage_t;

typedef struct {
    env_stage_t stage;
    float level; // 0..1
    float timer; // seconds in ENV_DELAY / ENV_HOLD / ENV_REST, and in ENV_DECAY when key-up triggered
    float phase; // synced cycling: 0..1 through the cycle
    float start; // synced cycling: the level the current attack started from
    uint8_t cycles; // cycles finished since it was triggered (it stops at the count)
} env_t;

// One envelope's settings, worked out once per layer and pass; the envelopes advance by any dt from them (the amp
// and filter envelopes several times a pass: synth_envelopes())
typedef struct {
    float attack, decay, release; // how fast the distance left to go shrinks: log2 per second (< 0: at once); on
                                  // the linear curve full scale per second, and the swell attack's growth
    float sustain;
    float hold, rest;             // seconds (cycling; hold also DAHDSR's)
    float delay, decay_s;         // seconds: DAHDSR's delay, and a key-up envelope's decay before it releases
    int cycle, curve, key_up;
    int count;                    // cycling: how many cycles, 0 = endless
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
    // From the last synth_update(), for the ticks (voice_cvs()), which add the envelopes, the LFOs and the fast
    // routes; in semitones but vca_slow
    float pitch_base;              // the gliding note with bend, tune, unison spread and drift
    float pitch_a, pitch_b;        // oscillator A's and B's offsets from it
    float pitch_slow;              // the slower routes into pitch A + B (scale quantize takes them with the LFOs)
    float to_pitch[2], to_filter[2]; // LFO 1 / LFO 2 depths into pitch and cutoff
    float pmod_env;                // filter envelope → oscillator A, at full level
    float cutoff_rest, cutoff_env; // the cutoff besides the envelope, LFOs and fast routes, and the envelope amount
    float vca_slow;                // the slower routes into the VCA gain
    float lfo_octaves[2];          // rate modulation of this voice's own LFO phases
    float lfo_age, lfo_gain[2];    // seconds since the note started, and each LFO's delay / fade gain for it now
    float bend;                    // per-voice pitch bend, -1..1 (the per-channel layer modes)
    float timbre;                  // CC 74, 0..1 (a mod source)
    float macro[2];                // the macros' values for this voice, -1..1 (routes into DEST_MACRO1 / 2)
    // Envelope stage routes, from the last pass: rate multipliers (attack, decay, release) and a sustain offset per
    // envelope, and whether any is in use; the glide coefficient with its route
    float env_rate_mul[4][3], env_sus_add[4];
    uint8_t env_modded;
    float glide_k;
} voice_t;

static voice_t voices[VOICE_COUNT] = {[0 ... VOICE_COUNT - 1] = {.lfo_gain = {1.0f, 1.0f}, .glide_k = 1.0f}};
static uint32_t allocations, random_state = 0x2545F491u;
static float bend[LAYERS], mod_wheel[LAYERS], aftertouch[LAYERS]; // per layer, for channel mode; bend -1..1
static float pedal[LAYERS], cc_source[2][LAYERS];                 // the pedal (0 / 1) and CC A / B (0..1) per layer
static int cc_number[2] = {11, 2};                                // CC A / B: expression and breath by default
static int bend_range = BEND_DEFAULT, detune = DETUNE_DEFAULT;
static uint8_t voice_mode[LAYERS], priority[LAYERS]; // VOICE_* and PRIORITY_* (synth.h)
static int drift_amount;                             // 0..99
static uint8_t scale, scale_root;                    // SCALE_* (0 off), root 0..11 (C..B)
static float morph_param[LAYERS][PARAM_COUNT];       // the morph target's knob values
static int morph_loaded, morph_target; // the target patch + 1, 0 = none
static int last_voice[2] = {-1, -1}; // per layer: the most recently played voice, for the single-wire destinations

// the panel's pots, smoothed (0..1), and each layer's parameters (the knobs, then Env 3 and Env 4)
static float panel[POT_COUNT];
static float pot_reading[POT_COUNT], pot_slope[POT_COUNT]; // the muxed pots' latest reading, and the glide to it
static float layer_param[LAYERS][PARAM_COUNT] = {[0][PARAM_LFO2_RATE] = 0.5f, [1][PARAM_LFO2_RATE] = 0.5f};
static uint8_t osc_tune_modes[LAYERS][2];
static int knob_held[POT_COUNT]; // each stepped knob's step
static int pots_primed;
static int edit_layer;
static uint8_t latched[PARAM_COUNT]; // its knob controls the value (per-layer parameters: the edit layer's)
static int8_t pickup_side[PARAM_COUNT]; // which side of the stored value the knob started on: -1, +1 (0 = unknown)
static float pickup_from[PARAM_COUNT];  // and where it was then (pickup off: moving away from there takes over)
static int pickup = 1; // 0: a knob that moves jumps the value to where it is, as on the stock synth
static int frozen; // MOD ASSIGN: no knob picks up, however close it is to its value
static uint32_t frozen_pots; // these knobs don't either (synth_freeze_pots())
static int env_select; // the ADSR knobs' envelopes: 0 filter and amp; 3 (Shift) Env 3 on the filter knobs, Env 4 on the amp's
static int shift;      // Shift held or latched: the non-ADSR knobs are frozen
static int keytrack_switch_seen = -1; // the keytrack switch's last position: a patch's value holds until it moves
static int8_t macro_offset[LAYERS][2];
static int lfo_select;      // which LFO the LFO knobs, the shape button and the routing buttons edit: 0 or 1
static uint8_t lfo_shape[LAYERS][2]; // lfo_shape_t, per layer and LFO
static uint8_t lfo2_dests[LAYERS];   // LFO2_TO_* bits
static uint8_t env_cycle[LAYERS];    // ENV_CYCLE_* bits
static uint8_t env_sync[LAYERS][4];  // amp, filter, Env 3, Env 4: 0 free, else the clock division + 1
static uint8_t env_trigger[LAYERS], env_dahdsr[LAYERS]; // a bit per envelope
static uint8_t env_curve[LAYERS];                       // 2 bits per envelope, ENV_CURVE_*
static uint8_t env_delay[LAYERS][4], env_hold[LAYERS][4]; // 0-99
static uint8_t env_count[LAYERS][4];                      // cycles, 0 = endless
static uint8_t env_tempo[LAYERS];                         // a bit per envelope: stage times in note values
static uint8_t layer_link;                                // LINK_* bits
static uint8_t lfo_restart_keys;                          // a sequence's keys restart the LFOs, not its steps

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

// which link each knob follows (0: none)
static const uint8_t link_of_param[PARAM_COUNT] = {
    [POT_CUTOFF] = LINK_FILTER, [POT_FIL_ENV_AMT] = LINK_FILTER,
    [POT_AMP_ATT] = LINK_ENV(0), [POT_AMP_DEC] = LINK_ENV(0), [POT_AMP_SUS] = LINK_ENV(0), [POT_AMP_REL] = LINK_ENV(0),
    [POT_FIL_ATT] = LINK_ENV(1), [POT_FIL_DEC] = LINK_ENV(1), [POT_FIL_SUS] = LINK_ENV(1), [POT_FIL_REL] = LINK_ENV(1),
    [PARAM_ENV3_ATT] = LINK_ENV(2), [PARAM_ENV3_DEC] = LINK_ENV(2), [PARAM_ENV3_SUS] = LINK_ENV(2), [PARAM_ENV3_REL] = LINK_ENV(2),
    [PARAM_ENV4_ATT] = LINK_ENV(3), [PARAM_ENV4_DEC] = LINK_ENV(3), [PARAM_ENV4_SUS] = LINK_ENV(3), [PARAM_ENV4_REL] = LINK_ENV(3),
    [POT_LFO_FREQ] = LINK_LFO(0), [POT_LFO_AMOUNT] = LINK_LFO(0),
    [PARAM_LFO2_RATE] = LINK_LFO(1), [PARAM_LFO2_AMOUNT] = LINK_LFO(1),
};

// buttons: a loaded patch's state holds until the button is toggled
static uint8_t switch_override[SWITCH_COUNT], switch_value[SWITCH_COUNT], switch_at_load[SWITCH_COUNT];
static int keytrack_setting = 2;
static route_t routes[LAYERS][ROUTES];
static uint16_t route_both[LAYERS]; // bit r: routes[layer][r] plays on the other layer's voices too

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

// Layer 1 has every voice: layers off, and the per-channel modes
static int one_layer(void) { return layer_mode == LAYER_OFF || layer_mode >= LAYER_VOICE_PER_CHANNEL; }
static int per_channel(void) { return layer_mode >= LAYER_VOICE_PER_CHANNEL; }

// Layer 1's share of the voices in the two-layer modes: as the patch sets it (1-7), or automatically 4 + 4, or 7 + 1
// (1 + 7) when exactly one layer plays mono or legato without unison, since that layer only ever sounds its first
// voice (a pad with one arp or lead voice on top). Voice mode, unison, layer mode and voice count changes all go
// through synth_all_off(), so a voice never changes layer while it's gated.
static int layer1_voices; // 0 = automatic
static int lone_mono(int layer) { return !unison && voice_mode[layer] != VOICE_POLY; }
static int voice_split(void)
{
    if (layer1_voices)
        return layer1_voices;
    int m1 = lone_mono(0), m2 = lone_mono(1);
    return m1 == m2 ? VOICE_COUNT / 2 : m1 ? 1 : VOICE_COUNT - 1;
}

static int layer_of(int voice) { return one_layer() || voice < voice_split() ? 0 : 1; }

static void layer_voices(int layer, int *first, int *end)
{
    if (one_layer()) {
        *first = 0;
        *end = VOICE_COUNT;
    } else {
        int split = voice_split();
        *first = layer ? split : 0;
        *end = layer ? VOICE_COUNT : split;
    }
}

// The links in force: layer 2 plays only in the two-layer modes
static unsigned links(void) { return one_layer() ? 0 : layer_link; }
static int linked(int layer, unsigned link) { return layer == 1 && (links() & link); }
static int env_layer(int layer, int e) { return linked(layer, LINK_ENV(e)) ? 0 : layer; }
static int lfo_layer(int layer, int w) { return linked(layer, LINK_LFO(w)) ? 0 : layer; }
static int param_layer(int layer, int param) { return linked(layer, link_of_param[param]) ? 0 : layer; }
// LFO w as `layer` hears it
static float lfo_read(int layer, int w, int voice)
{
    int l = lfo_layer(layer, w);
    return lfo_value(LFO_ID(l, w), voice, (lfo_shape_t)lfo_shape[l][w]);
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

int synth_layer_voices(void) { return layer1_voices; }

void synth_set_layer_voices(int n)
{
    n = n < 0 || n >= VOICE_COUNT ? 0 : n;
    if (n == layer1_voices)
        return;
    synth_all_off();
    layer1_voices = n;
}

void synth_set_split_note(int note) { split_note = note < 0 ? 0 : note > 127 ? 127 : note; }

int synth_edit_layer(void) { return edit_layer; }

static void unlatch(int param)
{
    latched[param] = 0;
    pickup_side[param] = 0;
}

oscillator_tune_mode_t synth_osc_tune_mode(int layer, int osc)
{
    return layer >= 0 && layer < LAYERS && osc >= 0 && osc < 2
         ? (oscillator_tune_mode_t)osc_tune_modes[layer][osc] : OSC_TUNE_SEMITONES;
}

void synth_set_osc_tune_mode(int layer, int osc, int mode)
{
    if (layer < 0 || layer >= LAYERS || osc < 0 || osc >= 2 || mode < 0 || mode >= OSC_TUNE_MODE_COUNT)
        return;
    osc_tune_modes[layer][osc] = (uint8_t)mode;
    int pot = osc == 0 ? POT_FREQ_A : POT_FREQ_B;
    unlatch(pot);
    int steps = mode == OSC_TUNE_SEMITONES ? (int)(2 * OSC_RANGE_SEMITONES) : mode == OSC_TUNE_OCTAVE ? 6 : 0;
    if (steps)
        knob_held[pot] = (int)(panel[pot] * (float)steps + 0.5f);
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
static int adsr_pot(int pot) { return (pot >= POT_AMP_REL && pot <= POT_AMP_ATT) || (pot >= POT_FIL_REL && pot <= POT_FIL_ATT); }

static int param_of_pot(int pot)
{
    static const uint8_t stage[] = {[POT_FIL_REL] = 3, [POT_FIL_SUS] = 2, [POT_FIL_DEC] = 1, [POT_FIL_ATT] = 0,
                                    [POT_AMP_REL] = 3, [POT_AMP_SUS] = 2, [POT_AMP_DEC] = 1, [POT_AMP_ATT] = 0};
    if (env_select > 0 && pot >= POT_FIL_REL && pot <= POT_FIL_ATT) // FIL REL, SUS, DEC, ATT
        return (env_select == 2 ? PARAM_ENV4_ATT : PARAM_ENV3_ATT) + stage[pot];
    if (env_select == 3 && pot >= POT_AMP_REL && pot <= POT_AMP_ATT)
        return PARAM_ENV4_ATT + stage[pot];
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

static unsigned key_up_envs(int layer)
{
    unsigned link = layer ? links() & 15u : 0;
    return (env_trigger[layer] & ~link) | (env_trigger[0] & link);
}
void synth_set_env_cycle(int layer, int bits) { env_cycle[layer] = (uint8_t)(bits & 15); }
int synth_env_tempo(int layer) { return env_tempo[layer]; }
void synth_set_env_tempo(int layer, int bits) { env_tempo[layer] = (uint8_t)(bits & 15); }
int synth_env_count(int layer, int env) { return env_count[layer][env & 3]; }
void synth_set_env_count(int layer, int env, int count) { env_count[layer][env & 3] = (uint8_t)(count < 0 ? 0 : count > 99 ? 99 : count); }

// A layer's envelopes whose key-up trigger changed: one that's running starts again as its new trigger has it (a
// held key's normal envelope attacks), else it releases, so none is left sustaining without a key.
static void retrigger_envs(int layer, unsigned changed)
{
    int first, end;
    layer_voices(layer, &first, &end);
    for (int v = first; v < end; v++) {
        env_t *envs[] = {&voices[v].amp, &voices[v].filter, &voices[v].env3, &voices[v].env4};
        for (int e = 0; e < 4; e++)
            if (changed >> e & 1 && envs[e]->stage != ENV_IDLE)
                envs[e]->stage = voices[v].gate && !(key_up_envs(layer) >> e & 1) ? ENV_ATTACK : ENV_RELEASE;
    }
}

int synth_env_trigger(int layer) { return env_trigger[layer]; }

void synth_set_env_trigger(int layer, int bits)
{
    unsigned before = key_up_envs(layer), before2 = key_up_envs(1);
    env_trigger[layer] = (uint8_t)(bits & 15);
    retrigger_envs(layer, before ^ key_up_envs(layer));
    if (!layer && links())
        retrigger_envs(1, before2 ^ key_up_envs(1)); // layer 2's linked envelopes follow
}

int synth_layer_link(void) { return layer_link; }

void synth_set_layer_link(int bits)
{
    unsigned before = key_up_envs(1);
    layer_link = (uint8_t)(bits & LINK_ALL);
    if (!one_layer())
        retrigger_envs(1, before ^ key_up_envs(1));
    if (edit_layer == 1)
        for (int i = 0; i < PARAM_COUNT; i++)
            if (link_of_param[i])
                unlatch(i); // their knobs now edit the other value
}

int synth_env_edit_layer(int env) { return env_layer(edit_layer, env); }
int synth_lfo_edit_layer(int which) { return lfo_layer(edit_layer, which); }
int synth_pot_linked(int pot) { return linked(edit_layer, link_of_param[param_of_pot(pot)]); }

int synth_env_dahdsr(int layer) { return env_dahdsr[layer]; }
void synth_set_env_dahdsr(int layer, int bits) { env_dahdsr[layer] = (uint8_t)(bits & 15); }
int synth_env_delay(int layer, int env) { return env_delay[layer][env & 3]; }
void synth_set_env_delay(int layer, int env, int value) { env_delay[layer][env & 3] = (uint8_t)(value < 0 ? 0 : value > 99 ? 99 : value); }
int synth_env_hold(int layer, int env) { return env_hold[layer][env & 3]; }
void synth_set_env_hold(int layer, int env, int value) { env_hold[layer][env & 3] = (uint8_t)(value < 0 ? 0 : value > 99 ? 99 : value); }
int synth_env_curve(int layer) { return env_curve[layer]; }

void synth_set_env_curve(int layer, int bits)
{
    for (int e = 0; e < 4; e++)
        if ((bits >> 2 * e & 3) >= ENV_CURVE_COUNT)
            bits &= ~(3 << 2 * e);
    env_curve[layer] = (uint8_t)bits;
}

int synth_lfo2_dests(int layer) { return lfo2_dests[layer]; }

void synth_set_lfo2_dests(int layer, int bits) { lfo2_dests[layer] = (uint8_t)(bits & 7); }

int synth_lfo2_toggle(int s)
{
    unsigned bit = s == SWITCH_LFO_FREQ ? LFO2_TO_PITCH : s == SWITCH_LFO_FILTER ? LFO2_TO_FILTER
                 : s == SWITCH_LFO_PW ? LFO2_TO_PW : 0;
    if (!bit)
        return -1;
    synth_hold_switch(s); // LFO 1 keeps its routing
    int layer = lfo_layer(edit_layer, 1);
    lfo2_dests[layer] ^= (uint8_t)bit;
    return (lfo2_dests[layer] & bit) != 0;
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
    if (!per_layer[param] || layer == param_layer(edit_layer, param))
        unlatch(param); // its knob picks it up from here
}

float synth_pot_value(int pot) { int p = param_of_pot(pot); return layer_param[param_layer(edit_layer, p)][p]; }

int synth_pot_pending(int pot) { return !latched[param_of_pot(pot)]; }

int synth_pickup(void) { return pickup; }
void synth_set_pickup(int on)
{
    if (!on == !pickup)
        return;
    pickup = on != 0;
    for (int i = 0; i < PARAM_COUNT; i++)
        if (!latched[i])
            pickup_side[i] = 0; // a knob already waiting starts from where it is now, so the change moves nothing
}

int synth_keytrack(void) { return keytrack_setting; }

void synth_set_keytrack(int amount) { keytrack_setting = amount < 0 ? 0 : amount > 2 ? 2 : amount; }

int synth_env_select(void) { return env_select; }

void synth_aftertouch(uint8_t value, int layers)
{
    for (int l = 0; l < LAYERS; l++)
        if (layers >> l & 1)
            aftertouch[l] = (float)value / 127.0f;
}

void synth_poly_pressure(uint8_t note, uint8_t value, int layers)
{
    for (int v = 0; v < VOICE_COUNT; v++)
        if (voices[v].gate && voices[v].note == note && layers >> layer_of(v) & 1)
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
            else {
                *x = (route_t){0};
                route_both[layer] &= (uint16_t)~(1u << r);
            }
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
    route_both[layer] &= (uint16_t)~(1u << free_slot);
    return 1;
}

int synth_route_both(int layer) { return route_both[layer]; }

void synth_set_route_both(int layer, int bits) { route_both[layer] = (uint16_t)bits; }

const route_t *synth_routes(int layer) { return routes[layer]; }

int synth_dest_for_pot(int pot)
{
    switch (pot) {
    case POT_FREQ_A: return DEST_PITCH_A;
    case POT_FREQ_B: case POT_FINE: return DEST_PITCH_B;
    case POT_MASTER_TUNE: return DEST_MACRO1;
    case POT_CUTOFF: return DEST_CUTOFF;
    case POT_FIL_ENV_AMT: return DEST_FIL_ENV_AMT;
    case POT_PMOD_FIL_ENV: return DEST_PMOD_ENV;
    case POT_LFO_AMOUNT: return lfo_select ? DEST_LFO2_AMOUNT : DEST_LFO_AMOUNT;
    case POT_LFO_FREQ: return lfo_select ? DEST_LFO2_RATE : DEST_LFO_RATE;
    case POT_AMP_SUS: return env_select == 3 ? DEST_ENV4_SUS : DEST_VCA;
    case POT_AMP_ATT: return env_select == 3 ? DEST_ENV4_ATT : DEST_AMP_ATT;
    case POT_AMP_DEC: return env_select == 3 ? DEST_ENV4_DEC : DEST_AMP_DEC;
    case POT_AMP_REL: return env_select == 3 ? DEST_ENV4_REL : DEST_AMP_REL;
    case POT_FIL_ATT: case POT_FIL_DEC: case POT_FIL_SUS: case POT_FIL_REL: {
        static const uint8_t stage[] = {[POT_FIL_ATT] = 0, [POT_FIL_DEC] = 1, [POT_FIL_SUS] = 2, [POT_FIL_REL] = 3};
        int first = env_select == 2 ? DEST_ENV4_ATT : env_select ? DEST_ENV3_ATT : DEST_FIL_ATT;
        return first + stage[pot];
    }
    case POT_GLIDE: return DEST_GLIDE;
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

void synth_freeze_knobs(int on)
{
    frozen = on;
    for (int i = 0; on && i < PARAM_COUNT; i++)
        if (i != POT_MASTER_VOL)
            unlatch(i);
}

void synth_freeze_pots(uint32_t mask)
{
    for (int i = 0; i < POT_COUNT; i++)
        if ((mask & ~frozen_pots) >> i & 1)
            unlatch(param_of_pot(i));
    frozen_pots = mask;
}

static int sw(switch_t s)
{
    int physical = controls_on(s);
    if (switch_override[s] && physical != switch_at_load[s])
        switch_override[s] = 0; // toggled since the patch was loaded
    return switch_override[s] ? switch_value[s] : physical;
}

int synth_switch(int s) { return s >= 0 && s < SWITCH_COUNT ? sw((switch_t)s) : 0; }

// For the host's engine-state probe (55): every byte 0-127. Per layer the held keys' count and the first four
// (mono, legato and unison retrigger only when it's 0), then per voice gate, amp and filter envelope stage, then the
// keytrack setting, the keytrack switch as last seen (+ 1, 0 unknown) and now, and per panel switch (0-15) whether
// the patch's value still overrides it, that value and the physical position.
int synth_probe(uint8_t *out)
{
    int n = 0;
    for (int l = 0; l < LAYERS; l++) {
        out[n++] = (uint8_t)held_count[l];
        for (int i = 0; i < 4; i++)
            out[n++] = i < held_count[l] ? held[l][i] & 0x7F : 0;
    }
    for (int v = 0; v < VOICE_COUNT; v++) {
        out[n++] = (uint8_t)voices[v].gate;
        out[n++] = (uint8_t)voices[v].amp.stage;
        out[n++] = (uint8_t)voices[v].filter.stage;
    }
    out[n++] = (uint8_t)keytrack_setting;
    out[n++] = (uint8_t)(keytrack_switch_seen + 1);
    out[n++] = (uint8_t)controls_keytrack();
    for (int s = 0; s < 16; s++)
        out[n++] = (uint8_t)(switch_override[s] | switch_value[s] << 1 | controls_on((switch_t)s) << 2);
    n += lfo_probe(&out[n]);
    return n;
}

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
        for (int osc = 0; osc < 2; osc++)
            osc_tune_modes[l][osc] = (patch->osc_tune[l] >> (2 * osc)) & 3;
        for (int r = 0; r < ROUTES; r++) {
            route_t x = patch->routes[l][r];
            routes[l][r] = x.source < SRC_COUNT && x.dest < DEST_COUNT ? x : (route_t){0};
        }
        for (int w = 0; w < 2; w++)
            lfo_shape[l][w] = patch->lfo_shape[l][w] < LFO_SHAPE_COUNT ? patch->lfo_shape[l][w] : 0;
        lfo2_dests[l] = patch->lfo2_dests[l] & 7;
        env_cycle[l] = patch->env_cycle[l] & 15;
        env_trigger[l] = patch->env_trigger[l] & 15;
        env_dahdsr[l] = patch->env_dahdsr[l] & 15;
        env_tempo[l] = patch->env_tempo[l] & 15;
        synth_set_env_curve(l, patch->env_curve[l]);
        for (int e = 0; e < 4; e++) {
            env_sync[l][e] = patch->env_sync[l][e] <= LFO_DIVISIONS ? patch->env_sync[l][e] : 0;
            synth_set_env_delay(l, e, patch->env_delay[l][e]);
            synth_set_env_hold(l, e, patch->env_hold[l][e]);
            synth_set_env_count(l, e, patch->env_count[l][e]);
        }
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
    keytrack_switch_seen = controls_keytrack();
    for (int l = 0; l < LAYERS; l++)
        for (int k = 0; k < 2; k++)
            macro_offset[l][k] = patch->macro_offset[l][k] < -99 ? -99 : patch->macro_offset[l][k];
    layer_mode = patch->layer_mode < LAYER_MODE_COUNT ? (layer_mode_t)patch->layer_mode : LAYER_OFF;
    layer1_voices = patch->layer_voices < VOICE_COUNT ? patch->layer_voices : 0;
    layer_link = patch->layer_link & LINK_ALL;
    lfo_restart_keys = patch->lfo_restart_keys != 0;
    for (int l = 0; l < LAYERS; l++)
        route_both[l] = patch->route_both[l];
    synth_set_split_note(patch->split);
    for (int w = 0; w < 2; w++) {
        lfo_sync[w] = patch->lfo_sync[w] < LFO_SYNC_COUNT ? (lfo_sync_t)patch->lfo_sync[w] : LFO_SYNC_FREE;
        lfo_trigger[w] = patch->lfo_trigger[w] < LFO_TRIGGER_COUNT ? (lfo_trigger_t)patch->lfo_trigger[w]
                                                                     : LFO_TRIGGER_FREE;
        lfo_cycles[w] = !patch->lfo_cycles[w] ? 1 : patch->lfo_cycles[w] > 99 ? 99 : patch->lfo_cycles[w];
        lfo_delay[w] = patch->lfo_delay[w] > 99 ? 99 : patch->lfo_delay[w];
        lfo_fade[w] = patch->lfo_fade[w] > 99 ? 99 : patch->lfo_fade[w];
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
        patch->osc_tune[l] = (uint8_t)(osc_tune_modes[l][0] | (osc_tune_modes[l][1] << 2));
        for (int r = 0; r < ROUTES; r++)
            patch->routes[l][r] = routes[l][r];
        for (int w = 0; w < 2; w++)
            patch->lfo_shape[l][w] = lfo_shape[l][w];
        patch->lfo2_dests[l] = lfo2_dests[l];
        patch->env_cycle[l] = env_cycle[l];
        patch->env_trigger[l] = env_trigger[l];
        patch->env_dahdsr[l] = env_dahdsr[l];
        patch->env_tempo[l] = env_tempo[l];
        patch->env_curve[l] = env_curve[l];
        for (int e = 0; e < 4; e++) {
            patch->env_sync[l][e] = env_sync[l][e];
            patch->env_delay[l][e] = env_delay[l][e];
            patch->env_hold[l][e] = env_hold[l][e];
            patch->env_count[l][e] = env_count[l][e];
        }
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
    for (int l = 0; l < LAYERS; l++)
        for (int k = 0; k < 2; k++)
            patch->macro_offset[l][k] = macro_offset[l][k];
    patch->layer_mode = (uint8_t)layer_mode;
    patch->layer_voices = (uint8_t)layer1_voices;
    patch->layer_link = layer_link;
    patch->lfo_restart_keys = lfo_restart_keys;
    for (int l = 0; l < LAYERS; l++)
        patch->route_both[l] = route_both[l];
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
        patch->lfo_cycles[w] = lfo_cycles[w];
        patch->lfo_delay[w] = lfo_delay[w];
        patch->lfo_fade[w] = lfo_fade[w];
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

// Within one layer's voices: the voice already on this note, then the oldest silent one, then the quietest released
// (so a long tail plays on while a nearly silent one is taken), then the oldest.
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
            if (ok && (best < 0 || (pass == 1 ? x->amp.level < voices[best].amp.level
                                               : x->started < voices[best].started)))
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

// This layer's notes come from a sequence whose keys restart the LFOs, so a note leaves them alone. (The one-voice-per-
// channel modes go past the sequence.)
static int keys_restart_lfos(int layer)
{
    return lfo_restart_keys && layer_mode != LAYER_VOICE_PER_CHANNEL && layer_mode != LAYER_MPE && seq_takes_layer(layer);
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
    if (retrigger || was_silent) {
        x->fresh = 1;
        if (!keys_restart_lfos(layer_of((int)(x - voices))))
            x->lfo_age = 0;
    }
    if (retrigger) {
        // from the current level, so a stolen voice doesn't click (ENV_DELAY goes on to the attack at once without
        // a delay); key-up envelopes fade out instead, until the key is let go
        int up = key_up_envs(layer_of((int)(x - voices)));
        env_t *envs[] = {&x->amp, &x->filter, &x->env3, &x->env4};
        for (int i = 0; i < 4; i++) {
            if (up >> i & 1) {
                if (envs[i]->stage != ENV_IDLE)
                    envs[i]->stage = ENV_RELEASE;
                continue;
            }
            envs[i]->stage = ENV_DELAY;
            envs[i]->timer = 0;
            envs[i]->phase = 0; // synced cycling starts its cycle here
            envs[i]->start = envs[i]->level;
            envs[i]->cycles = 0;
        }
    }
}

// The key is let go (key_up), or the voice is silenced (synth_all_off()): key-up envelopes start only on a key-up
static void gate_off(voice_t *x, int key_up)
{
    x->gate = 0;
    int up = key_up ? key_up_envs(layer_of((int)(x - voices))) : 0;
    env_t *envs[] = {&x->amp, &x->filter, &x->env3, &x->env4};
    for (int i = 0; i < 4; i++)
        if (up >> i & 1) {
            envs[i]->stage = ENV_DELAY;
            envs[i]->timer = 0;
            envs[i]->phase = 0;
            envs[i]->start = envs[i]->level;
            envs[i]->cycles = 0;
            if (i == 0)
                x->fresh = 1; // its pitch goes out before the VCA opens
        } else if (envs[i]->stage != ENV_IDLE)
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

// A note started on voice v of `layer`. A linked LFO is one LFO for both layers, so "the first key" counts both.
static void lfos_note_on(int layer, int v, int first)
{
    if (keys_restart_lfos(layer))
        return;
    for (int w = 0; w < 2; w++)
        lfo_note_on(LFO_ID(lfo_layer(layer, w), w), v,
                    first && !(linked(1, LINK_LFO(w)) && layer_gated(1 - layer)));
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
            lfos_note_on(layer, v, !was_held);
        }
        last_voice[layer] = first;
        return;
    }
    int was_gated = layer_gated(layer);
    int v = allocate(layer, note);
    gate_on(&voices[v], note, velocity, 1);
    lfos_note_on(layer, v, !was_gated);
    last_voice[layer] = v;
}

int synth_lfo_restart_keys(void) { return lfo_restart_keys; }

void synth_set_lfo_restart_keys(int on) { lfo_restart_keys = on != 0; }

void synth_lfos_key(int layers, int busy)
{
    for (int l = 0; l < LAYERS; l++) {
        if (!(layers >> l & 1) || (l && one_layer()) || !keys_restart_lfos(l))
            continue;
        int first, end;
        layer_voices(l, &first, &end);
        for (int v = first; v < end; v++)
            voices[v].lfo_age = 0;
        for (int w = 0; w < 2; w++) { // a linked LFO is one LFO for both layers, so "the first key" counts both
            int other = linked(1, LINK_LFO(w)) && (busy >> (1 - l) & 1 || layer_gated(1 - l));
            lfo_key_on(LFO_ID(lfo_layer(l, w), w), !(busy >> l & 1) && !other);
        }
    }
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
    if (layers & 2 && !one_layer())
        layer_note_on(1, note, velocity);
}

void synth_note_on(uint8_t note, uint8_t velocity, int channel)
{
    synth_note_on_layers(note, velocity, synth_key_layers(note, channel));
}

int synth_channel_layers(int channel) { return layer_mode != LAYER_CHANNEL ? 3 : channel == 1 ? 2 : 1; }

int synth_channel_voice(int channel)
{
    if (layer_mode == LAYER_VOICE_PER_CHANNEL)
        return channel < VOICE_COUNT ? channel : -1;
    if (layer_mode == LAYER_MPE)
        return channel >= 1 && channel <= VOICE_COUNT ? channel - 1 : -1;
    return -1;
}

void synth_set_pedal(int layers, int down)
{
    for (int l = 0; l < LAYERS; l++)
        if (layers >> l & 1)
            pedal[l] = down ? 1.0f : 0;
}

void synth_cc_source(int which, uint8_t value, int layers)
{
    for (int l = 0; l < LAYERS; l++)
        if (layers >> l & 1)
            cc_source[which & 1][l] = (float)value / 127.0f;
}

int synth_cc_number(int which) { return cc_number[which & 1]; }

void synth_set_cc_number(int which, int number) { cc_number[which & 1] = number < 1 ? 1 : number > 127 ? 127 : number; }

int synth_source_for_pot(int pot)
{
    switch (pot) {
    case POT_LFO_FREQ: case POT_LFO_AMOUNT: return lfo_select ? SRC_LFO2 : SRC_LFO;
    case POT_FIL_ATT: case POT_FIL_DEC: case POT_FIL_SUS: case POT_FIL_REL:
        return env_select == 2 ? SRC_ENV4 : env_select ? SRC_ENV3 : SRC_FILTER_ENV;
    case POT_AMP_ATT: case POT_AMP_DEC: case POT_AMP_SUS: case POT_AMP_REL: return env_select == 3 ? SRC_ENV4 : SRC_AMP_ENV;
    default: return SRC_NONE;
    }
}

void synth_set_shift(int on)
{
    if (!on == !shift)
        return;
    shift = on;
    env_select = on ? 3 : 0;
    for (int i = 0; i < POT_COUNT; i++) // the ADSR knobs change meaning, the rest will pick up after
        if (i != POT_MASTER_VOL) {
            unlatch(param_of_pot(i));
            if (adsr_pot(i))
                unlatch(i); // their plain parameter too
        }
}

int synth_shift(void) { return shift; }

int synth_macro_offset(int layer, int which) { return macro_offset[layer & 1][which & 1]; }

void synth_set_macro_offset(int layer, int which, int value)
{
    macro_offset[layer & 1][which & 1] = (int8_t)(value < -99 ? -99 : value > 99 ? 99 : value);
}

void synth_copy_layer(int from, int to)
{
    if (from == to || from < 0 || from >= LAYERS || to < 0 || to >= LAYERS)
        return;
    for (int i = 0; i < PARAM_COUNT; i++)
        if (per_layer[i])
            layer_param[to][i] = layer_param[from][i];
    for (int r = 0; r < ROUTES; r++) // a both-layers route already plays on `to`: a copy would count it twice
        routes[to][r] = route_both[from] >> r & 1 ? (route_t){0} : routes[from][r];
    route_both[to] = 0;
    for (int w = 0; w < 2; w++) {
        lfo_shape[to][w] = lfo_shape[from][w];
        lfo_pattern[LFO_ID(to, w)] = lfo_pattern[LFO_ID(from, w)];
        lfo_step_count[LFO_ID(to, w)] = lfo_step_count[LFO_ID(from, w)];
        macro_offset[to][w] = macro_offset[from][w];
    }
    lfo2_dests[to] = lfo2_dests[from];
    env_cycle[to] = env_cycle[from];
    synth_set_env_trigger(to, env_trigger[from]);
    env_dahdsr[to] = env_dahdsr[from];
    env_tempo[to] = env_tempo[from];
    env_curve[to] = env_curve[from];
    for (int e = 0; e < 4; e++) {
        env_sync[to][e] = env_sync[from][e];
        env_delay[to][e] = env_delay[from][e];
        env_hold[to][e] = env_hold[from][e];
        env_count[to][e] = env_count[from][e];
    }
    if (voice_mode[to] != voice_mode[from])
        synth_all_off(); // the voice split may change (voice_split())
    voice_mode[to] = voice_mode[from];
    priority[to] = priority[from];
    if (to == edit_layer)
        for (int i = 0; i < PARAM_COUNT; i++)
            if (per_layer[i])
                unlatch(i);
}

int synth_channel_plays(int channel)
{
    return layer_mode != LAYER_VOICE_PER_CHANNEL || channel < VOICE_COUNT;
}

void synth_voice_note_on(int voice, uint8_t note, uint8_t velocity)
{
    if (voice < 0 || voice >= VOICE_COUNT)
        return;
    int first = !layer_gated(0);
    gate_on(&voices[voice], note, velocity, 1);
    lfo_note_on(LFO_ID(0, 0), voice, first);
    lfo_note_on(LFO_ID(0, 1), voice, first);
    last_voice[0] = voice;
}

void synth_voice_note_off(int voice, uint8_t note)
{
    if (voice >= 0 && voice < VOICE_COUNT && voices[voice].gate && voices[voice].note == note)
        gate_off(&voices[voice], 1);
}

void synth_voice_bend(int voice, int value)
{
    if (voice >= 0 && voice < VOICE_COUNT)
        voices[voice].bend = (float)(value - 8192) / 8192.0f;
}

void synth_voice_pressure(int voice, uint8_t value)
{
    if (voice >= 0 && voice < VOICE_COUNT)
        voices[voice].pressure = (float)value / 127.0f;
}

void synth_voice_timbre(int voice, uint8_t value)
{
    if (voice >= 0 && voice < VOICE_COUNT)
        voices[voice].timbre = (float)value / 127.0f;
}

void synth_timbre(uint8_t value, int layers)
{
    for (int v = 0; v < VOICE_COUNT; v++)
        if (layers >> layer_of(v) & 1)
            voices[v].timbre = (float)value / 127.0f;
}

void synth_note_off_layers(uint8_t note, int layers)
{
    for (int layer = 0; layer < LAYERS; layer++) {
        if (!(layers >> layer & 1) || (layer && one_layer()))
            continue; // (with layers off, layer 1 has every voice)
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
                    gate_off(&voices[v], 1);
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
                gate_off(&voices[v], 1);
    }
}

void synth_all_off(void)
{
    held_count[0] = held_count[1] = 0;
    for (int v = 0; v < VOICE_COUNT; v++)
        gate_off(&voices[v], 0);
}

void synth_pitch_bend(int value, int layers)
{
    for (int l = 0; l < LAYERS; l++)
        if (layers >> l & 1)
            bend[l] = (float)(value - 8192) / 8192.0f;
}

int synth_bend_range(void) { return bend_range; }
void synth_set_bend_range(int semitones) { bend_range = semitones < 1 ? 1 : semitones > 12 ? 12 : semitones; }
int synth_detune(void) { return detune; }
void synth_set_detune(int cents) { detune = cents < 0 ? 0 : cents > 50 ? 50 : cents; }

void synth_mod_wheel(uint8_t value, int layers)
{
    for (int l = 0; l < LAYERS; l++)
        if (layers >> l & 1)
            mod_wheel[l] = (float)value / 127.0f;
}

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
// left at the end; the swell's 2^5 growth) or a fall (to -60 dB: 1/1000 left), or with log2_left = 1 on the linear
// curve, full scale in that time. step() turns an exponential one into the multiplier for dt.
static float stage_rate(float time, float log2_left)
{
    return time <= 0 ? -1.0f : log2_left / time;
}

static float step(float rate, float dt)
{
    return rate < 0 ? 0 : fast_exp2(-dt * rate);
}

float synth_env_extra_time(int value) { return knob_time((float)value / 99.0f, 0, ATTACK_MAX_S); }

// Stage times in note values: envelope e of `layer` takes them (not while it cycles synced: its shares rule)
static int tempo_env(int layer, int e) { return env_tempo[layer] >> e & 1 && !(env_cycle[layer] >> e & 1 && env_sync[layer][e]); }

int synth_env_note_step(float knob)
{
    int i = (int)(knob * (float)LFO_DIVISIONS + 0.5f); // 0, then 1/32 .. 8 bars
    return i < 0 ? 0 : i > LFO_DIVISIONS ? LFO_DIVISIONS : i;
}

const char *synth_env_note_name(int step) { return step <= 0 ? "0" : lfo_division_name(LFO_DIVISIONS - step); }

float synth_env_note_seconds(int step)
{
    return step <= 0 ? 0 : lfo_division_quarters(LFO_DIVISIONS - step) * lfo_quarter_seconds();
}

int synth_env_tempo_step(int env, int stage, float knob)
{
    int layer = env_layer(edit_layer, env);
    if (!tempo_env(layer, env) || (stage == 2 && !(env_cycle[layer] >> env & 1)))
        return -1; // seconds, or the sustain level
    return synth_env_note_step(knob);
}

// A synced cycle's shares of its period, from the knobs as weights (attack, hold = SUS, fall = DEC, rest = REL)
static void cycle_shares(const float *p, const uint8_t *knobs, float share[4])
{
    float w[4] = {p[knobs[0]], p[knobs[2]], p[knobs[1]], p[knobs[3]]};
    float sum = w[0] + w[1] + w[2] + w[3];
    for (int i = 0; i < 4; i++)
        share[i] = sum < 0.001f ? 0.25f : w[i] / sum;
}

// Envelope e of `layer` from the knobs `p`
static void env_rates(env_rates_t *r, const float *p, int layer, int e)
{
    const uint8_t *knobs = env_params[e];
    int att = knobs[0], dec = knobs[1], sus = knobs[2], rel = knobs[3];
    int cycle = env_cycle[layer] >> e & 1, sync = env_sync[layer][e], dahdsr = !cycle && env_dahdsr[layer] >> e & 1;
    int curve = env_curve[layer] >> 2 * e & 3, linear = curve == ENV_CURVE_LINEAR;
    float fall = linear ? 1.0f : 9.966f; // log2(1000)
    r->cycle = cycle;
    r->curve = curve;
    r->key_up = (int)(key_up_envs(layer) >> e & 1);
    r->count = cycle ? env_count[layer][e] : 0;
    int notes = tempo_env(layer, e);
#define STAGE_TIME(stage, knob, cyc) (notes ? synth_env_note_seconds(synth_env_note_step(knob)) : synth_env_time(stage, knob, cyc))
#define EXTRA_TIME(value) (notes ? synth_env_note_seconds(synth_env_note_step((float)(value) / 99.0f)) : synth_env_extra_time(value))
    r->attack = stage_rate(STAGE_TIME(0, p[att], cycle),
                           linear ? 1.0f : curve == ENV_CURVE_SWELL ? SWELL_OCTAVES : 1.585f); // log2(3)
    r->decay_s = STAGE_TIME(1, p[dec], cycle);
    r->decay = stage_rate(r->decay_s, fall);
    r->release = cycle ? r->decay : stage_rate(STAGE_TIME(3, p[rel], 0), fall);
    r->sustain = p[sus];
    r->hold = cycle ? STAGE_TIME(2, p[sus], 1) : dahdsr ? EXTRA_TIME(env_hold[layer][e]) : 0;
    r->rest = cycle ? STAGE_TIME(3, p[rel], 1) : 0;
    r->delay = dahdsr ? EXTRA_TIME(env_delay[layer][e]) : 0;
#undef STAGE_TIME
#undef EXTRA_TIME
    r->synced = cycle && sync > 0;
    if (r->synced) {
        float share[4];
        cycle_shares(p, knobs, share);
        r->period = lfo_division_quarters(sync - 1) * lfo_quarter_seconds();
        r->a = share[0];
        r->h = share[1];
        r->f = share[2];
        r->release = stage_rate(r->f * r->period, fall); // key up: falls with its share of the cycle
    }
}

// The attack's and the fall's shapes over their stage (u 0..1), for synced cycling
static float attack_shape(int curve, float u)
{
    return curve == ENV_CURVE_LINEAR ? u
         : curve == ENV_CURVE_SWELL ? (fast_exp2(u * SWELL_OCTAVES) - 1.0f) * SWELL_OFFSET
         : ATTACK_OVERSHOOT * (1.0f - fast_exp2(-u * 1.585f));
}

static float fall_shape(int curve, float u)
{
    return curve == ENV_CURVE_LINEAR ? 1.0f - u : fast_exp2(-u * 9.966f);
}

// The count of cycles is done: a key-up envelope ends, a key-down one stays at rest until the key is let go
static int cycles_done(env_t *e, const env_rates_t *r)
{
    if (!r->count || e->cycles < r->count)
        return 0;
    e->level = 0;
    e->stage = r->key_up ? ENV_IDLE : ENV_REST;
    return 1;
}

// Synced cycling: the level is a function of the phase through the cycle
static void env_synced(env_t *e, float dt, const env_rates_t *r)
{
    if (cycles_done(e, r))
        return;
    e->phase += dt / r->period;
    if (e->phase >= 1.0f) {
        e->phase -= (float)(int)e->phase;
        e->start = 0;
        if (e->cycles < 255)
            e->cycles++;
        if (cycles_done(e, r))
            return;
    }
    float x = e->phase;
    if (x < r->a) {
        e->stage = ENV_ATTACK;
        e->level = e->start + (1.0f - e->start) * attack_shape(r->curve, x / r->a);
    } else if ((x -= r->a) < r->h) {
        e->stage = ENV_HOLD;
        e->level = 1.0f;
    } else if ((x -= r->h) < r->f) {
        e->stage = ENV_DECAY;
        e->level = fall_shape(r->curve, x / r->f);
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
    int layer = env_layer(edit_layer, env);
    if (!(env_cycle[layer] >> env & 1) || !env_sync[layer][env])
        return -1;
    float p[PARAM_COUNT];
    for (int i = 0; i < PARAM_COUNT; i++)
        p[i] = layer_param[layer][i];
    p[env_params[env][stage]] = knob;
    float share[4];
    cycle_shares(p, env_params[env], share);
    static const uint8_t slot[4] = {0, 2, 1, 3}; // attack, decay = fall, sustain = hold, release = rest
    return share[slot[stage]];
}

// A fall towards `target` at `rate` (exponential, or linear: full scale per second)
static float fall_to(float level, float target, float rate, float dt, int linear)
{
    if (rate < 0)
        return target;
    if (!linear)
        return target + (level - target) * step(rate, dt);
    float d = rate * dt;
    return level > target ? (level - d > target ? level - d : target) : (level + d < target ? level + d : target);
}

static void env_update(env_t *e, float dt, const env_rates_t *r)
{
    if (r->synced && e->stage != ENV_IDLE && e->stage != ENV_RELEASE) {
        env_synced(e, dt, r);
        return;
    }
    int linear = r->curve == ENV_CURVE_LINEAR;
    switch (e->stage) {
    case ENV_IDLE:
        e->level = 0;
        break;
    case ENV_DELAY: // the level goes on falling as in a release meanwhile
        e->timer += dt;
        if (e->timer < r->delay) {
            e->level = fall_to(e->level, 0, r->release, dt, linear);
            break;
        }
        e->stage = ENV_ATTACK;
        // fall through
    case ENV_ATTACK:
        if (r->attack < 0 || r->attack * dt > 24.0f)
            e->level = 1.0f;
        else if (linear)
            e->level += r->attack * dt;
        else if (r->curve == ENV_CURVE_SWELL)
            e->level = (e->level + SWELL_OFFSET) * fast_exp2(r->attack * dt) - SWELL_OFFSET;
        else
            e->level = ATTACK_OVERSHOOT - (ATTACK_OVERSHOOT - e->level) * step(r->attack, dt);
        if (e->level >= 1.0f) {
            e->level = 1.0f;
            e->stage = r->cycle || r->hold > 0 ? ENV_HOLD : ENV_DECAY;
            e->timer = 0;
        }
        break;
    case ENV_HOLD:
        e->timer += dt;
        if (e->timer >= r->hold) {
            e->stage = ENV_DECAY;
            e->timer = 0;
        }
        break;
    case ENV_DECAY:
        if (!r->cycle) { // then follows the sustain knob while held; a key-up envelope releases after its decay
            e->level = fall_to(e->level, r->sustain, r->decay, dt, linear);
            e->timer += dt;
            if (r->key_up && e->timer >= r->decay_s)
                e->stage = ENV_RELEASE;
            break;
        }
        e->level = fall_to(e->level, 0, r->decay, dt, linear);
        if (e->level < CYCLE_FLOOR) {
            e->level = 0;
            e->stage = ENV_REST;
            e->timer = 0;
            if (e->cycles < 255)
                e->cycles++;
            cycles_done(e, r); // the last cycle ends with its fall
        }
        break;
    case ENV_REST:
        e->timer += dt;
        if (!r->cycle)
            e->stage = ENV_DECAY; // cycling switched off: settle on the sustain level
        else if (!cycles_done(e, r) && e->timer >= r->rest)
            e->stage = ENV_ATTACK;
        break;
    case ENV_RELEASE:
        e->level = fall_to(e->level, 0, r->release, dt, linear);
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

// Stepped knobs (FREQ A / B; LFO FREQ while its LFO is clock-synced; an envelope's time knobs while its stage times
// are note values): how many steps, 0 for the others
static int knob_steps(int pot)
{
    if (pot == POT_FREQ_A || pot == POT_FREQ_B) {
        int osc = pot == POT_FREQ_A ? 0 : 1;
        int mode = osc_tune_modes[edit_layer][osc];
        return mode == OSC_TUNE_SEMITONES ? (int)(2 * OSC_RANGE_SEMITONES) : mode == OSC_TUNE_OCTAVE ? 6 : 0;
    }
    if (adsr_pot(pot)) {
        int p = param_of_pot(pot);
        for (int e = 0; e < 4; e++)
            for (int stage = 0; stage < 4; stage++)
                if (env_params[e][stage] == p)
                    return synth_env_tempo_step(e, stage, 0) >= 0 ? LFO_DIVISIONS : 0; // 0 and the divisions
        return 0;
    }
    return pot == POT_LFO_FREQ && lfo_sync[lfo_select] == LFO_SYNC_CLOCK ? LFO_DIVISIONS - 1 : 0;
}

// A stepped knob's step moves only STEP_HYSTERESIS of a step past the half-way point, so pot noise at an edge can't
// flip it back and forth; the knob then reads as the middle of its step, so the engine, the display and a saved
// patch all round it the same way.
#define STEP_HYSTERESIS 0.25f

float synth_knob_snap(int pot, float knob)
{
    int steps = knob_steps(pot);
    if (!steps)
        return knob;
    float x = knob * (float)steps;
    if (x > (float)knob_held[pot] + 0.5f + STEP_HYSTERESIS || x < (float)knob_held[pot] - 0.5f - STEP_HYSTERESIS) {
        knob_held[pot] = (int)(x + 0.5f);
        if (knob_held[pot] < 0) knob_held[pot] = 0;
        if (knob_held[pot] > steps) knob_held[pot] = steps;
    }
    return (float)knob_held[pot] / (float)steps;
}

int synth_knob_semitones(float knob) { return (int)semitone_steps(knob); }

float synth_knob_tune_value(int layer, int osc, float knob)
{
    switch (synth_osc_tune_mode(layer, osc)) {
    case OSC_TUNE_OCTAVE: return 12.0f * (float)((int)(knob * 6.0f + 0.5f) - 3); // -3..3 octaves, in semitones
    case OSC_TUNE_FREE: return (knob - 0.5f) * 72.0f;
    case OSC_TUNE_FIXED: return 24.0f + knob * 84.0f;
    default: return semitone_steps(knob);
    }
}

static float env_amount_of(float knob) { float x = (knob - 0.5f) * 2; return x * (x < 0 ? -x : x); } // -1..1, squared
float synth_knob_env_amount(float knob) { return env_amount_of(knob) * FILTER_ENV_SEMITONES; }
float synth_knob_pmod(float knob) { return knob * knob * PMOD_ENV_SEMITONES; }

float synth_knob_macro(float knob)
{
    float x = (knob - 0.5f) * 2;
    float a = x < 0 ? -x : x;
    a = a <= MACRO_DEAD_ZONE ? 0 : (a - MACRO_DEAD_ZONE) / (1.0f - MACRO_DEAD_ZONE);
    return x < 0 ? -a : a;
}
float synth_knob_cutoff_hz(float knob) { return 440.0f * fast_exp2((CUTOFF_LOW_NOTE + knob * CUTOFF_SPAN - 69.0f) / 12.0f); }
float synth_knob_glide_s(float knob) { return knob < 0.02f ? 0 : 0.002f * fast_exp2(knob * 10.0f); }

int synth_knob_cents(float knob)
{
    float cents = (knob - 0.5f) * 2 * FINE_SEMITONES * 100;
    return (int)(cents < 0 ? cents - 0.5f : cents + 0.5f);
}

static void read_panel(float dt)
{
    int track = controls_keytrack(); // keyboard tracking: a loaded patch's value holds until the switch moves
    if (track != keytrack_switch_seen) {
        keytrack_switch_seen = track;
        keytrack_setting = track;
    }

    float k = dt >= POT_SMOOTHING_S || !pots_primed ? 1.0f : dt / POT_SMOOTHING_S;
    for (int i = 0; i < POT_COUNT; i++) {
        float raw = (float)controls_pot(i) / 4095.0f;
        if (knob_steps(i)) { // stepped: the hysteresis takes the noise, no smoothing
            panel[i] = synth_knob_snap(i, raw);
        } else if (i >= POT_CUTOFF || !pots_primed) { // read every pass: smoothed a little
            panel[i] += (raw - panel[i]) * k;
        } else { // behind the muxes, a new reading every MUX_SCAN_S: glide to it over that time instead of stepping
            if (raw != pot_reading[i]) {
                pot_reading[i] = raw;
                pot_slope[i] = (raw - panel[i]) / MUX_SCAN_S;
            }
            float step = pot_slope[i] * dt, left = raw - panel[i];
            panel[i] = (left < 0 ? -left : left) <= (step < 0 ? -step : step) ? raw : panel[i] + step;
        }
        int p = param_of_pot(i);
        if (!pots_primed) { // both layers start from the panel
            layer_param[0][p] = layer_param[1][p] = panel[i];
            latched[p] = 1;
            continue;
        }
        int layer = per_layer[p] ? param_layer(edit_layer, p) : 0; // shared knobs keep one value, in both layers
        if (!latched[p] && !frozen && !(frozen_pots >> i & 1) && !(shift && !adsr_pot(i))) {
            float diff = panel[i] - layer_param[layer][p];
            int8_t side = diff < 0 ? -1 : 1;
            if (diff < PICKUP_WINDOW && diff > -PICKUP_WINDOW)
                latched[p] = 1;
            else if (pickup_side[p] == 0) {
                pickup_side[p] = side;
                pickup_from[p] = panel[i];
            } else if (side != pickup_side[p])
                latched[p] = 1; // swept past it
            else if (!pickup) { // jump: moved since it started waiting (a stepped knob by a step)
                float moved = panel[i] - pickup_from[p];
                if (knob_steps(i) ? moved != 0 : moved > JUMP_MOVE || moved < -JUMP_MOVE)
                    latched[p] = 1;
            }
        }
        if (latched[p]) {
            if (per_layer[p])
                layer_param[layer][p] = panel[i];
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
    case SRC_LFO: return lfo_read(layer, 0, voice) * x->lfo_gain[0];
    case SRC_LFO2: return lfo_read(layer, 1, voice) * x->lfo_gain[1];
    case SRC_LFO_UNI:
        return 0.5f * (1.0f + lfo_read(layer, 0, voice) * x->lfo_gain[0]);
    case SRC_LFO2_UNI:
        return 0.5f * (1.0f + lfo_read(layer, 1, voice) * x->lfo_gain[1]);
    case SRC_GATE: return x->gate ? 1.0f : 0;
    case SRC_TIMBRE: return x->timbre;
    case SRC_BEND: {
        float b = bend[layer] + (per_channel() ? x->bend : 0);
        return b < -1 ? -1 : b > 1 ? 1 : b;
    }
    case SRC_PEDAL: return pedal[layer];
    case SRC_CC_A: return cc_source[0][layer];
    case SRC_CC_B: return cc_source[1][layer];
    case SRC_VOICE: {
        int first, end;
        layer_voices(layer, &first, &end);
        return end - first > 1 ? (float)(voice - first) / (float)(end - first - 1) : 0;
    }
    case SRC_FIXED: return 1.0f;
    case SRC_MACRO1: return x->macro[0];
    case SRC_MACRO2: return x->macro[1];
    case SRC_FILTER_ENV: return x->filter.level;
    case SRC_AMP_ENV: return x->amp.level;
    case SRC_VELOCITY: return (float)x->velocity / 127.0f;
    case SRC_MOD_WHEEL: return mod_wheel[layer];
    case SRC_AFTERTOUCH: return x->pressure > aftertouch[layer] ? x->pressure : aftertouch[layer]; // poly, channel
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
    return source == SRC_LFO || source == SRC_LFO2 || source == SRC_RANDOM || source == SRC_NOTE
        || (source >= SRC_SEQ_A && source <= SRC_SEQ_D) || source == SRC_BEND || source == SRC_MACRO1
        || source == SRC_MACRO2;
}

// The routes the fast ticks evaluate (synth_tick()): from the envelopes and LFOs into pitch, cutoff and the VCA.
// The rest, slower sources and fast ones into depths, rates and the single wires, go once per pass.
static int fast_route(const route_t *r)
{
    int source = r->source;
    int fast_source = source == SRC_LFO || source == SRC_LFO2 || source == SRC_FILTER_ENV || source == SRC_AMP_ENV
                   || source == SRC_ENV3 || source == SRC_ENV4 || source == SRC_LFO_UNI || source == SRC_LFO2_UNI;
    return fast_source && (r->dest <= DEST_CUTOFF || r->dest == DEST_VCA);
}

// Adds the routes of `layer` evaluated for `voice` into mod[] (DEST_COUNT entries): the fast ones (fast_route()) or
// the others. The VCA is a gain on the amp envelope (1 + mod, 0..1): a bipolar source at depth 99 swings it from
// closed to open (tremolo, gates), and a 0..1 source counts down from full (source - 1), so velocity → VCA at 99 is
// plain velocity sensitivity. FIX (always 1) would count down nothing, so into the VCA or the volume it turns the
// level down by its depth, either sign: 50 is half, a layer's level.
static int macro_source(int source) { return source == SRC_MACRO1 || source == SRC_MACRO2; }

// The destinations that are added up over both layers for a single wire. A both-layers route counts there once: on the
// layer with the newest note, as a single wire follows the newest note.
static int summed_wire(int dest)
{
    return (dest >= DEST_A_LEVEL && dest <= DEST_LFO_TO_PW) || dest == DEST_LFO2_TO_PW || dest == DEST_VOLUME;
}

static int newest_layer(void)
{
    int a = last_voice[0], b = last_voice[1];
    return b < 0 ? 0 : a < 0 ? 1 : (int32_t)(voices[b].started - voices[a].started) > 0;
}

static void evaluate_routes(int layer, int voice, float *mod, int fast)
{
    // the slow routes in two phases: everything into the macros first, then the routes the macros feed
    for (int phase = 0; phase < (fast ? 1 : 2); phase++) {
        if (phase == 1)
            for (int k = 0; k < 2; k++) {
                float m = mod[DEST_MACRO1 + k] + (float)macro_offset[layer][k] / 99.0f
                        + (k ? 0 : synth_knob_macro(layer_param[layer][POT_MASTER_TUNE]));
                voices[voice].macro[k] = m < -1 ? -1 : m > 1 ? 1 : m;
            }
        for (int n = 0; n < 2 * ROUTES; n++) { // its own routes, then the other layer's both-layers ones
            int owner = n < ROUTES ? layer : 1 - layer, r = n % ROUTES, both = route_both[owner] >> r & 1;
            if (n == ROUTES && (one_layer() || !route_both[owner]))
                break;
            if (owner != layer && !both)
                continue;
            const route_t *x = &routes[owner][r];
            if (x->source == SRC_NONE || x->dest >= DEST_COUNT || fast_route(x) != fast
                || macro_source(x->source) != phase
                || (both && !one_layer() && summed_wire(x->dest) && layer != newest_layer()))
                continue;
            float value = source_value(&voices[voice], voice, layer, x->source);
            float depth = (float)x->depth / 99.0f;
            if ((x->dest == DEST_VCA || x->dest == DEST_VOLUME) && !bipolar(x->source)) {
                value -= 1.0f;
                if (x->source == SRC_FIXED) { // down by the depth
                    value = -1.0f;
                    depth = depth < 0 ? -depth : depth;
                }
            }
            if (x->dest <= DEST_PITCH) // pitch: a squared depth curve, fine near zero for vibrato
                depth *= depth < 0 ? -depth : depth;
            mod[x->dest] += value * depth;
        }
    }
}

static float clamp01(float x) { return x < 0 ? 0 : x > 1 ? 1 : x; }

typedef struct {
    float coarse_a, coarse_b, fine_b, glide_s, glide_k, env_amount, pmod_env, cutoff, depth, depth2;
} layer_params_t;
static layer_params_t lp[LAYERS]; // from the last synth_update()
static float last_mod[LAYERS][DEST_COUNT]; // the layer-level routes (from its most recent voice), per pass
static void voice_slow(int v, float dt);

// From the last synth_update(), for the ticks: each envelope's rates, and each LFO's rate knob and rate modulation
static env_rates_t rates[LAYERS][4]; // amp, filter, Env 3, Env 4
static float lfo_knob[LAYERS][2], lfo_octaves[LAYERS][2];
static float keytrack_amount; // 0, 0.5, 1
static int lfo_to_pitch, lfo_to_filter; // the LFO routing buttons
// and what the ticks can skip: a layer with no fast route (fast_route()), and one whose routes never read Env 3 / 4
static uint8_t fast_routes[LAYERS], uses_env34[LAYERS];
// Each LFO's value this tick, for the voices sharing its phase (per-voice phases are read per voice)
static float lfo_now[LAYERS][2];

static void sample_lfos(void)
{
    for (int l = 0; l < LAYERS; l++)
        for (int w = 0; w < 2; w++)
            if (!lfo_voice_phases(w))
                lfo_now[l][w] = lfo_read(l, w, -1);
}

// Voice v's CVs from the last synth_update()'s terms plus its envelopes, LFOs and fast routes now: the VCA, the
// cutoff when `cutoff`, the pitch when `pitch`
static void voice_cvs(int v, int cutoff, int pitch)
{
    const voice_t *x = &voices[v];
    if (x->amp.stage == ENV_IDLE) { // silent: its VCA is closed, and its pitch and cutoff can't be heard
        sh_set(CV_AMP1 + v, 0);
        return;
    }
    int l = layer_of(v);
    const layer_params_t *q = &lp[l];
    float mod[DEST_COUNT] = {0};
    if (fast_routes[l])
        evaluate_routes(l, v, mod, 1);
    float lfo = 0, lfo2 = 0;
    if (cutoff || pitch) {
        lfo = lfo_voice_phases(0) ? lfo_read(l, 0, v) : lfo_now[l][0];
        lfo2 = lfo_voice_phases(1) ? lfo_read(l, 1, v) : lfo_now[l][1];
        lfo *= x->lfo_gain[0];
        lfo2 *= x->lfo_gain[1];
    }
    if (cutoff) {
        float note = x->cutoff_rest + lfo * x->to_filter[0] + lfo2 * x->to_filter[1]
                   + mod[DEST_CUTOFF] * MOD_CUTOFF_SEMITONES + x->cutoff_env * x->filter.level;
        sh_set(CV_FIL1 + v, tuning_note_cv(TUNING_ROW_FILTER + v, note));
    }
    float gain = x->amp.level * clamp01(1.0f + x->vca_slow + mod[DEST_VCA]);
    sh_set(CV_AMP1 + v, gain > 0 ? cv_from(VCA_THRESHOLD + (1.0f - VCA_THRESHOLD) * gain) : 0);
    if (pitch) {
        float pitch_mod = lfo * x->to_pitch[0] + lfo2 * x->to_pitch[1] + x->pitch_slow
                        + mod[DEST_PITCH] * MOD_PITCH_SEMITONES;
        float pitch_mod_a = pitch_mod, pitch_mod_b = pitch_mod;
        int fixed_a = osc_tune_modes[l][0] == OSC_TUNE_FIXED;
        int fixed_b = osc_tune_modes[l][1] == OSC_TUNE_FIXED;
        if (scale) {
            float anchor_a = fixed_a ? q->coarse_a : (float)x->note;
            float anchor_b = fixed_b ? q->coarse_b : (float)x->note;
            pitch_mod_a = quantize(anchor_a + pitch_mod) - anchor_a;
            pitch_mod_b = quantize(anchor_b + pitch_mod) - anchor_b;
        }
        float pitch_adjust = x->pitch_base - x->pitch;
        float a = (fixed_a ? x->pitch_a + pitch_adjust : x->pitch_base + x->pitch_a) + pitch_mod_a
                + x->pmod_env * x->filter.level + mod[DEST_PITCH_A] * MOD_PITCH_SEMITONES;
        float b = (fixed_b ? x->pitch_b + pitch_adjust : x->pitch_base + x->pitch_b) + pitch_mod_b
                + mod[DEST_PITCH_B] * MOD_PITCH_SEMITONES;
        sh_set(CV_OSC_A1 + v, tuning_note_cv(TUNING_ROW_OSC_A + v, a));
        sh_set(CV_OSC_B1 + v, tuning_note_cv(TUNING_ROW_OSC_B + v, b));
    }
}

void synth_tick(float dt, int cutoff, unsigned pitch_voices)
{
    if (dt > 0.05f)
        dt = 0.05f;
    for (int l = 0; l < LAYERS; l++) // LFO_ID(0, 0) first: it moves the clock position
        for (int w = 0; w < 2; w++)
            lfo_update(LFO_ID(l, w), dt, lfo_knob[l][w], lfo_octaves[l][w]);
    sample_lfos();
    for (int v = 0; v < VOICE_COUNT; v++) {
        voice_t *x = &voices[v];
        int l = layer_of(v);
        for (int w = 0; w < 2; w++)
            if (lfo_voice_phases(w))
                lfo_update_voice(LFO_ID(lfo_layer(l, w), w), v, dt, x->lfo_octaves[w]);
        if (x->lfo_age < 100.0f)
            x->lfo_age += dt;
        x->lfo_gain[0] = lfo_fade_gain(0, x->lfo_age);
        x->lfo_gain[1] = lfo_fade_gain(1, x->lfo_age);
        env_t *envs[4] = {&x->amp, &x->filter, &x->env3, &x->env4};
        for (int e = 0; e < (uses_env34[l] ? 4 : 2); e++) { // Env 3 / 4 wait where they are until a route reads them
            if (!x->env_modded) {
                env_update(envs[e], dt, &rates[l][e]);
                continue;
            }
            env_rates_t r = rates[l][e]; // this voice's stage routes applied
            r.attack *= x->env_rate_mul[e][0];
            r.decay *= x->env_rate_mul[e][1];
            r.release *= x->env_rate_mul[e][2];
            r.hold /= x->env_rate_mul[e][1];
            r.decay_s /= x->env_rate_mul[e][1];
            r.rest /= x->env_rate_mul[e][2];
            r.sustain = clamp01(r.sustain + x->env_sus_add[e]);
            env_update(envs[e], dt, &r);
        }
        voice_cvs(v, cutoff, pitch_voices >> v & 1);
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

void synth_prepare_voices(unsigned mask)
{
    for (int v = 0; v < VOICE_COUNT; v++)
        if (mask >> v & 1)
            voice_slow(v, 0);
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
    lfo_to_pitch = sw(SWITCH_LFO_FREQ);
    lfo_to_filter = sw(SWITCH_LFO_FILTER);

    // routes evaluated for each layer's most recently played voice: the single-wire destinations, and the rate of
    // each LFO's shared phase
    for (int l = 0; l < LAYERS; l++) {
        for (int d = 0; d < DEST_COUNT; d++)
            last_mod[l][d] = 0;
        if (last_voice[l] >= 0 && (!one_layer() || l == 0))
            evaluate_routes(l, last_voice[l], last_mod[l], 0);
    }

    // the knob values as heard: morphed towards the morph target by the Morph destination
    static float eff[LAYERS][PARAM_COUNT];
    for (int l = 0; l < LAYERS; l++) {
        float m = morph_loaded ? clamp01(last_mod[l][DEST_MORPH]) : 0;
        for (int i = 0; i < PARAM_COUNT; i++) {
            int from = param_layer(l, i); // layer 1's value when linked
            eff[l][i] = m > 0 && i != POT_MASTER_VOL ? layer_param[from][i] + (morph_param[from][i] - layer_param[from][i]) * m
                                                     : layer_param[from][i];
        }
    }

    for (int l = 0; l < LAYERS; l++) {
        const float *p = eff[l];
        for (int e = 0; e < 4; e++)
            env_rates(&rates[l][e], p, env_layer(l, e), e);
        // LFOs (lfo.c, advanced in the ticks): rate or clock division from the rate knobs
        lfo_knob[l][0] = p[POT_LFO_FREQ];
        lfo_knob[l][1] = p[PARAM_LFO2_RATE];
        lfo_octaves[l][0] = last_mod[l][DEST_LFO_RATE] * MOD_LFO_OCTAVES;
        lfo_octaves[l][1] = last_mod[l][DEST_LFO2_RATE] * MOD_LFO_OCTAVES;
        fast_routes[l] = uses_env34[l] = 0;
        for (int n = 0; n < 2 * ROUTES; n++) { // as evaluate_routes()
            int owner = n < ROUTES ? l : 1 - l, r = n % ROUTES;
            if (owner != l && (one_layer() || !(route_both[owner] >> r & 1)))
                continue;
            const route_t *x = &routes[owner][r];
            if (x->source == SRC_NONE || x->dest >= DEST_COUNT)
                continue;
            fast_routes[l] |= fast_route(x);
            uses_env34[l] |= x->source == SRC_ENV3 || x->source == SRC_ENV4;
        }
        float glide = synth_knob_glide_s(p[POT_GLIDE]); // time constant, s
        lp[l] = (layer_params_t){
            .coarse_a = synth_knob_tune_value(l, 0, p[POT_FREQ_A]),
            .coarse_b = synth_knob_tune_value(l, 1, p[POT_FREQ_B]),
            .fine_b = (p[POT_FINE] - 0.5f) * 2 * FINE_SEMITONES,
            .glide_s = glide,
            .glide_k = glide > 0 ? 1.0f - fast_exp2(-dt / glide * 1.4427f) : 1.0f,
            .env_amount = env_amount_of(p[POT_FIL_ENV_AMT]), // -1..1
            .pmod_env = pmod_freq_a ? p[POT_PMOD_FIL_ENV] * p[POT_PMOD_FIL_ENV] : 0,
            .cutoff = CUTOFF_LOW_NOTE + p[POT_CUTOFF] * CUTOFF_SPAN,
            .depth = p[POT_LFO_AMOUNT] * p[POT_LFO_AMOUNT] + mod_wheel[l],
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
    const voice_t *recent = &voices[last_voice[0] >= 0 ? last_voice[0] : 0];
    float lfo_pw = (lfo_value(LFO_ID(0, 0), -1, (lfo_shape_t)lfo_shape[0][0]) * pw_depth * recent->lfo_gain[0]
                    + lfo_value(LFO_ID(0, 1), -1, (lfo_shape_t)lfo_shape[0][1]) * pw2_depth * recent->lfo_gain[1])
                 * LFO_PW_DEPTH;
    sh_set(CV_VOL_A, cv_from(shared[POT_A_LEVEL] + shared_mod[DEST_A_LEVEL]));
    sh_set(CV_VOL_B, cv_from(shared[POT_B_LEVEL] + shared_mod[DEST_B_LEVEL]));
    sh_set(CV_NOISE, cv_from(shared[POT_NOISE] + shared_mod[DEST_NOISE]));
    sh_set(CV_RESONANCE, cv_from(shared[POT_RESONANCE] + shared_mod[DEST_RESONANCE]));
    uint16_t pmod_osc_b = cv_from(shared[POT_PMOD_OSC_B] + shared_mod[DEST_PMOD_OSC_B]);
    sh_set(CV_PMOD_OSC_B, pmod_osc_b);
    sh_set(CV_MASTER_VOL, cv_from(panel[POT_MASTER_VOL] * clamp01(1.0f + shared_mod[DEST_VOLUME])));
    sh_set(CV_EXT_FIL, 0);
    // square off: PW at maximum, where the pulse fills the cycle (at the minimum it's still ~5 % wide and heard,
    // issue 7). Osc B's pulse held high is a DC offset in the poly-mod source, though (+103 cents on FREQ A at a
    // quarter amount, +746 on the filter at half), so while poly-mod from osc B reaches either, B goes to the
    // minimum, as p600fw does
    float pw_a = PW_CV_BOTTOM + PW_CV_RANGE * clamp01(shared[POT_A_PW] + shared_mod[DEST_A_PW] * MOD_PW) + lfo_pw;
    float pw_b = PW_CV_BOTTOM + PW_CV_RANGE * clamp01(shared[POT_B_PW] + shared_mod[DEST_B_PW] * MOD_PW) + lfo_pw;
    sh_set(CV_PW_A, sw(SWITCH_A_SQUARE) ? cv_from(pw_a) : 0xFFFF);
    int pmod_from_b = pmod_osc_b && (pmod_freq_a || sw(SWITCH_PMOD_FILTER));
    sh_set(CV_PW_B, sw(SWITCH_B_SQUARE) ? cv_from(pw_b) : pmod_from_b ? 0 : 0xFFFF);
    sh_gate(GATE_SAW_A, sw(SWITCH_A_SAW));
    sh_gate(GATE_TRI_A, sw(SWITCH_A_TRI));
    sh_gate(GATE_SAW_B, sw(SWITCH_B_SAW));
    sh_gate(GATE_TRI_B, sw(SWITCH_B_TRI));
    sh_gate(GATE_SYNC, sw(SWITCH_SYNC));
    sh_gate(GATE_PMOD_FREQ_A, pmod_freq_a);
    sh_gate(GATE_PMOD_FILTER, sw(SWITCH_PMOD_FILTER));

    keytrack_amount = 0.5f * (float)keytrack_setting;
    sample_lfos(); // for voice_cvs() below
    for (int v = 0; v < VOICE_COUNT; v++)
        voice_slow(v, dt);
}

// Voice v's once-a-pass work from the last synth_update()'s layer terms: glide and drift advance by dt, the slow
// routes, and the terms the ticks build on; then its CVs now. Also for a voice that started between two ticks
// (synth_prepare_voices(), dt 0), so its first pitch is its own and not the voice's last note's.
static void voice_slow(int v, float dt)
{
    {
        voice_t *x = &voices[v];
        int l = layer_of(v);
        const layer_params_t *q = &lp[l];
        if (dt > 0)
            x->pitch += ((float)x->note - x->pitch) * x->glide_k;
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

        // the terms the ticks build on (voice_cvs()); the fast routes they evaluate themselves
        float mod[DEST_COUNT] = {0};
        evaluate_routes(l, v, mod, 0);
        x->lfo_octaves[0] = mod[DEST_LFO_RATE] * MOD_LFO_OCTAVES;
        x->lfo_octaves[1] = mod[DEST_LFO2_RATE] * MOD_LFO_OCTAVES;

        int first, end;
        layer_voices(l, &first, &end);
        int n = end - first;
        float cents = (float)detune + last_mod[l][DEST_DETUNE] * 50.0f;
        float spread = unison && n > 1 ? ((float)(v - first) / (float)(n - 1) - 0.5f) * cents / 100.0f : 0;
        // glide and the envelope stages with their routes (times scale by 2^(4 × mod): 16× at full depth)
        x->glide_k = mod[DEST_GLIDE] != 0 && q->glide_s > 0
                   ? 1.0f - fast_exp2(-dt / (q->glide_s * fast_exp2(4.0f * mod[DEST_GLIDE])) * 1.4427f) : q->glide_k;
        x->env_modded = 0;
        for (int e = 0; e < 4; e++) {
            const float *m = &mod[DEST_AMP_ATT + 4 * e]; // attack, decay, sustain, release
            x->env_modded |= m[0] != 0 || m[1] != 0 || m[2] != 0 || m[3] != 0;
            x->env_rate_mul[e][0] = m[0] ? fast_exp2(-4.0f * m[0]) : 1.0f;
            x->env_rate_mul[e][1] = m[1] ? fast_exp2(-4.0f * m[1]) : 1.0f;
            x->env_rate_mul[e][2] = m[3] ? fast_exp2(-4.0f * m[3]) : 1.0f;
            x->env_sus_add[e] = m[2];
        }
        float depth = clamp01(q->depth + mod[DEST_LFO_AMOUNT]);
        float depth2 = clamp01(q->depth2 + mod[DEST_LFO2_AMOUNT]);
        x->to_pitch[0] = clamp01(depth * lfo_to_pitch + mod[DEST_LFO_TO_PITCH]) * LFO_PITCH_SEMITONES;
        x->to_filter[0] = clamp01(depth * lfo_to_filter + mod[DEST_LFO_TO_FILTER]) * LFO_FILTER_SEMITONES;
        x->to_pitch[1] = clamp01(depth2 * (float)((lfo2_dests[lfo_layer(l, 1)] & LFO2_TO_PITCH) != 0) + mod[DEST_LFO2_TO_PITCH])
                       * LFO_PITCH_SEMITONES;
        x->to_filter[1] = clamp01(depth2 * (float)((lfo2_dests[lfo_layer(l, 1)] & LFO2_TO_FILTER) != 0)
                                  + mod[DEST_LFO2_TO_FILTER]) * LFO_FILTER_SEMITONES;
        x->pmod_env = (q->pmod_env + mod[DEST_PMOD_ENV]) * PMOD_ENV_SEMITONES;
        x->pitch_slow = mod[DEST_PITCH] * MOD_PITCH_SEMITONES;
        float bent = bend[l] * (float)bend_range; // the layer's (MPE: the master channel's)
        if (per_channel())
            bent += x->bend * (layer_mode == LAYER_MPE ? MPE_BEND_SEMITONES : (float)bend_range);
        x->pitch_base = x->pitch + bent + spread + x->drift[0] * drift * DRIFT_PITCH_SEMITONES;
        x->pitch_a = q->coarse_a + mod[DEST_PITCH_A] * MOD_PITCH_SEMITONES;
        x->pitch_b = q->coarse_b + q->fine_b + mod[DEST_PITCH_B] * MOD_PITCH_SEMITONES;
        x->cutoff_rest = q->cutoff + keytrack_amount * (x->pitch - 60.0f) + mod[DEST_CUTOFF] * MOD_CUTOFF_SEMITONES
                       + x->drift[1] * drift * DRIFT_CUTOFF_SEMITONES;
        x->cutoff_env = (q->env_amount + mod[DEST_FIL_ENV_AMT]) * FILTER_ENV_SEMITONES;
        x->vca_slow = mod[DEST_VCA];
        voice_cvs(v, 1, 1);
    }
}
