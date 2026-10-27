// The LFO: free-running or locked to MIDI clock, with retrigger modes including a separate phase per voice.
#ifndef LFO_H
#define LFO_H

#include <stdint.h>

typedef enum { LFO_SYNC_FREE, LFO_SYNC_CLOCK, LFO_SYNC_COUNT } lfo_sync_t;
typedef enum { LFO_TRIGGER_FREE, LFO_TRIGGER_FIRST_KEY, LFO_TRIGGER_EVERY_KEY, LFO_TRIGGER_PER_VOICE,
               LFO_TRIGGER_COUNT } lfo_trigger_t;

extern lfo_sync_t lfo_sync;
extern lfo_trigger_t lfo_trigger;

// MIDI realtime: timing clock (24 per quarter note, `cycles` = cycle counter at arrival), start, stop
void lfo_clock_tick(uint32_t cycles);
void lfo_clock_start(void);
void lfo_clock_stop(void);

// A note started on `voice`; `first` when no other key was held.
void lfo_note_on(int voice, int first);
// Advances every phase by dt seconds; `knob` is LFO FREQ (0..1): the rate when free, the division when synced.
void lfo_update(float dt, float knob);
// -1..1: the voice's own LFO in per-voice mode, the shared one otherwise (voice -1 always the shared one).
float lfo_value(int voice, int square);

int lfo_division(float knob);
const char *lfo_division_name(int division); // 4 display characters

#endif
