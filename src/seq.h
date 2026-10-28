// The patch's step sequence, played from a key: pressing a key starts it at step 1, transposed so an offset of 0 is
// that key; it loops (or plays once) while keys are held, a new key transposes it from the next step, and letting
// go of every key stops it. Up to four automation lanes run with it as mod sources (SRC_SEQ_A..D). Edited from the
// host only; stored with the patch (store.c).
#ifndef SEQ_H
#define SEQ_H

#include <stdint.h>

#define SEQ_MAX_STEPS 64
#define SEQ_LANES     4
#define SEQ_BYTES     240 // steps × (3 + lanes) must fit: 64 steps alone, 48 with 2 lanes, 32 with 4

#define SEQ_ENABLED    1u // flags
#define SEQ_CLOCK      2u // tempo from MIDI clock (lfo.c), else bpm
#define SEQ_ONE_SHOT   4u // else loops
#define SEQ_LAYER_SHIFT 3 // bits 3-4: 0 keys of both layers, 1 layer 1's keys, 2 layer 2's

#define SEQ_GATE_TIE 100  // a gate of 100 % holds the note into the next step (legato)
#define SEQ_LANE_ZERO 64  // lane values 0..127, 64 = 0

typedef struct {
    uint8_t length;   // 1..SEQ_MAX_STEPS
    uint8_t lanes;    // 0..SEQ_LANES
    uint8_t division; // step length: an LFO clock division (lfo.h)
    uint8_t bpm;      // without the clock
    uint8_t flags;
    uint8_t swing;    // 0..75 %: even steps longer, odd shorter
    uint8_t smooth;   // bit l: lane l glides from step to step
    uint8_t note[SEQ_MAX_STEPS];     // offset + 64, in semitones
    uint8_t velocity[SEQ_MAX_STEPS]; // 0 = the key's
    uint8_t gate[SEQ_MAX_STEPS];     // bit 7 on, bits 0-6 percent of the step (SEQ_GATE_TIE ties)
    uint8_t lane[SEQ_LANES][SEQ_MAX_STEPS];
} sequence_t;

extern sequence_t sequence;

int seq_max_steps(int lanes); // what fits in SEQ_BYTES
void seq_clear(void);         // empty and off (the init sound)
void seq_fix(void);           // clamps the header to what fits

// MIDI keys: 1 if the sequence took the key (the caller doesn't play it). `layers` is where the key would go
// (synth_key_layers()).
int seq_key_on(uint8_t note, uint8_t velocity, int layers);
int seq_key_off(uint8_t note);
void seq_stop(void); // releases what's sounding
void seq_update(float dt);
float seq_lane(int lane); // -1..1 while playing, else 0

#endif
