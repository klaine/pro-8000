// The LFOs, two per layer: free-running or locked to MIDI clock (shared), with retrigger modes including a
// separate phase per voice, and seven shapes. Sync and retrigger are set per LFO (LFO 1 / LFO 2), for both layers.
#ifndef LFO_H
#define LFO_H

#include <stdint.h>

#define LFO_COUNT 4                                  // two per layer
#define LFO_ID(layer, which) ((which) * 2 + (layer)) // which: 0 = LFO 1, 1 = LFO 2

typedef enum { LFO_SYNC_FREE, LFO_SYNC_CLOCK, LFO_SYNC_COUNT } lfo_sync_t;
typedef enum { LFO_TRIGGER_FREE, LFO_TRIGGER_FIRST_KEY, LFO_TRIGGER_EVERY_KEY, LFO_TRIGGER_PER_VOICE,
               LFO_TRIGGER_COUNT } lfo_trigger_t;
// Every shape is bipolar (-1..1) and starts low, except square (high first) and ramp down (from the top).
// Sample & hold takes a new random value every cycle; drift glides smoothly from one random value to the next.
// Steps plays the LFO's step pattern over one cycle: +1 for a step that's on, -1 for one that's off, with an 8 ms
// slope at each change so a gate doesn't click.
typedef enum { LFO_TRIANGLE, LFO_SQUARE, LFO_RAMP_UP, LFO_RAMP_DOWN, LFO_SINE, LFO_SAMPLE_HOLD, LFO_DRIFT,
               LFO_STEPS, LFO_SHAPE_COUNT } lfo_shape_t;

#define LFO_MAX_STEPS 16
#define LFO_DEFAULT_STEPS   16
#define LFO_DEFAULT_PATTERN 0x3535u // bit n = step n: 1 0 1 0 1 1 0 0, twice: the "ta-ta-taa" trance gate

// Step patterns, per LFO (LFO_ID): bit n on = step n high; the count is 1..LFO_MAX_STEPS.
extern uint16_t lfo_pattern[LFO_COUNT];
extern uint8_t lfo_step_count[LFO_COUNT];

extern lfo_sync_t lfo_sync[2];       // per LFO 1 / LFO 2
extern lfo_trigger_t lfo_trigger[2];

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
// Advances the LFO's shared phase by dt seconds; `knob` is its rate knob (0..1): the rate when free, the division
// when synced. `octaves` speeds it up (or down) by modulation. Call for every LFO, LFO 0 first, once per update.
void lfo_update(int lfo, float dt, float knob, float octaves);
// Advances one voice's own phase (used in per-voice retrigger mode) at the rate lfo_update() set, × 2^octaves.
void lfo_update_voice(int lfo, int voice, float dt, float octaves);
// -1..1: the voice's own phase in per-voice mode, the LFO's shared one otherwise (voice -1 always the shared one).
float lfo_value(int lfo, int voice, lfo_shape_t shape);

#define LFO_DIVISIONS 15 // 8 bars .. 1/32
float lfo_rate_hz(float knob); // a free-running LFO's rate knob: 0.05-51 Hz
int lfo_division(float knob);
const char *lfo_division_name(int division); // 4 display characters
float lfo_division_quarters(int division);   // its length in quarter notes
// Seconds per quarter note from the MIDI clock (the last tempo once it stops; 120 BPM before any clock).
float lfo_quarter_seconds(void);
const char *lfo_shape_name(lfo_shape_t shape); // for the display

#endif
