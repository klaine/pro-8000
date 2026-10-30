// The LFOs, two per layer: free-running or locked to MIDI clock (shared), with retrigger modes including a
// separate phase per voice and one shots, and nine shapes. Sync and retrigger are set per LFO (LFO 1 / LFO 2), for both
// layers.
#ifndef LFO_H
#define LFO_H

#include <stdint.h>

#define LFO_COUNT 4                                  // two per layer
#define LFO_ID(layer, which) ((which) * 2 + (layer)) // which: 0 = LFO 1, 1 = LFO 2

typedef enum { LFO_SYNC_FREE, LFO_SYNC_CLOCK, LFO_SYNC_COUNT } lfo_sync_t;
// One shot (0.9.3): every key restarts the LFO, it runs lfo_cycles cycles and stays at the end of the last one. The
// shared phase, or with ONE_SHOT_VOICE each voice's own.
typedef enum { LFO_TRIGGER_FREE, LFO_TRIGGER_FIRST_KEY, LFO_TRIGGER_EVERY_KEY, LFO_TRIGGER_PER_VOICE,
               LFO_TRIGGER_ONE_SHOT, LFO_TRIGGER_ONE_SHOT_VOICE, LFO_TRIGGER_COUNT } lfo_trigger_t;
// Every shape is bipolar (-1..1) and starts low, except square (high first) and ramp down (from the top).
// Sample & hold takes a new random value every cycle; drift glides smoothly from one random value to the next.
// Steps plays the LFO's step pattern over one cycle: +1 for a step that's on, -1 for one that's off, with an 8 ms
// slope at each change so a gate doesn't click. Noise (0.9.0) is random values at 16 × the LFO's rate, each glided to
// from the last: a rough random wander at low rates, a fast jitter at the top (up to about 800 values a second).
typedef enum { LFO_TRIANGLE, LFO_SQUARE, LFO_RAMP_UP, LFO_RAMP_DOWN, LFO_SINE, LFO_SAMPLE_HOLD, LFO_DRIFT,
               LFO_STEPS, LFO_NOISE, LFO_SHAPE_COUNT } lfo_shape_t;

#define LFO_MAX_STEPS 16
#define LFO_DEFAULT_STEPS   16
#define LFO_DEFAULT_PATTERN 0x7575u // bit n = step n: 1 0 1 0 1 1 1 0, twice: a trance gate

// Step patterns, per LFO (LFO_ID): bit n on = step n high; the count is 1..LFO_MAX_STEPS.
extern uint16_t lfo_pattern[LFO_COUNT];
extern uint8_t lfo_step_count[LFO_COUNT];

extern lfo_sync_t lfo_sync[2];       // per LFO 1 / LFO 2
extern lfo_trigger_t lfo_trigger[2];
extern uint8_t lfo_cycles[2]; // per LFO, 1-99: how many cycles a one shot runs (the other retriggers ignore it)
// Every voice has a phase of its own
static inline int lfo_voice_phases(int which)
{
    return lfo_trigger[which] == LFO_TRIGGER_PER_VOICE || lfo_trigger[which] == LFO_TRIGGER_ONE_SHOT_VOICE;
}
// Per LFO, 0-99: a delay after a note starts before it fades in, and the fade's length (0 = none; lfo_seconds()).
extern uint8_t lfo_delay[2], lfo_fade[2];
float lfo_seconds(uint8_t value); // 0 for 0, else 0.02 s at 1 up to about 5 s at 99
// The LFO's gain for a voice `age` seconds after its note started: 0 through the delay, then up to 1 over the fade
float lfo_fade_gain(int which, float age);

// MIDI realtime, called from the USB or DIN interrupt: timing clock (24 per quarter note, `cycles` = cycle counter at
// arrival), start (the next timing clock is the downbeat), continue (from the song position, if one came, else from
// where the clock stopped), stop; and Song Position Pointer (in sixteenths), for the next continue
void lfo_clock_tick(uint32_t cycles);
void lfo_clock_start(void);
void lfo_clock_continue(void);
void lfo_clock_stop(void);
void lfo_clock_song_position(uint16_t sixteenths);

// A note started on `voice` of the LFO's layer; `first` when no other key of that layer was held.
void lfo_note_on(int lfo, int voice, int first);
// A key went down that a sequence plays from, when the keys restart the LFOs instead of its steps: the same restarts
// as lfo_note_on(), and in the per-voice modes every voice's phase together (steps move from voice to voice).
void lfo_key_on(int lfo, int first);
// For the host's probe: per LFO_ID the shared phase in % (a stopped one shot: 100), its finished cycles, then each
// voice's phase in %. Returns the byte count (40).
int lfo_probe(uint8_t *out);
// Advances the LFO's shared phase by dt seconds; `knob` is its rate knob (0..1): the rate when free, the division
// when synced. `octaves` speeds it up (or down) by modulation. Call for every LFO, LFO 0 first, once per update.
void lfo_update(int lfo, float dt, float knob, float octaves);
// Advances one voice's own phase (used in per-voice retrigger mode) at the rate lfo_update() set, × 2^octaves.
void lfo_update_voice(int lfo, int voice, float dt, float octaves);
// -1..1: the voice's own phase in per-voice mode, the LFO's shared one otherwise (voice -1 always the shared one).
// A one shot that has stopped holds the shape's value at the end of its last cycle.
float lfo_value(int lfo, int voice, lfo_shape_t shape);

#define LFO_DIVISIONS 15 // 8 bars .. 1/32
float lfo_rate_hz(float knob); // a free-running LFO's rate knob: 0.05-51 Hz
int lfo_division(float knob);
const char *lfo_division_name(int division); // 4 display characters
float lfo_division_quarters(int division);   // its length in quarter notes
// Seconds per quarter note from the MIDI clock (the last tempo once it stops; 120 BPM before any clock).
float lfo_quarter_seconds(void);
float lfo_beat_phase(void); // 0..1 through the current quarter note (the clock position), for a beat light
const char *lfo_shape_name(lfo_shape_t shape); // for the display

#endif
