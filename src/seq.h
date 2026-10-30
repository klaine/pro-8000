// The patch's step sequence, played from a key: pressing a key starts it at step 1, transposed so an offset of 0 is
// that key; it loops (or plays once) while keys are held, a new key transposes it from the next step, and letting
// go of every key stops it. In arpeggio mode the steps walk the held notes instead (up, down, up-down, random, as
// played), each step's offset transposing the note it plays. Steps can play with a probability and as 2-4 ratchets.
// Up to four automation lanes run with it as mod sources (SRC_SEQ_A..D). The last one or two can instead be chord notes
// (0.9.3): each step then plays up to three notes, sharing its gate, velocity, probability and ratchets. Edited from
// the host only; stored with the patch (store.c).
#ifndef SEQ_H
#define SEQ_H

#include <stdint.h>

#define SEQ_MAX_STEPS 64
#define SEQ_LANES     4
#define SEQ_BYTES     240 // steps × (4 + lanes) must fit: 60 steps alone, 48 with 1 lane, 30 with 4

#define SEQ_ENABLED    1u // flags
#define SEQ_CLOCK      2u // tempo from MIDI clock (lfo.c), else bpm
#define SEQ_ONE_SHOT   4u // else loops
#define SEQ_LAYER_SHIFT 3 // bits 3-4: 0 keys of both layers, 1 layer 1's keys, 2 layer 2's
#define SEQ_CHORD_SHIFT 5 // bits 5-6: how many of the lanes, the last ones, are chord notes (0-2); firmware before
                          // 0.9.3 ignores them and plays the step's own note
#define SEQ_CHORD(flags)  ((flags) >> SEQ_CHORD_SHIFT & 3)
#define SEQ_CHORD_MAX 2

#define SEQ_GATE_TIE 100  // a gate of 100 % holds the note into the next step (legato)
#define SEQ_LANE_ZERO 64  // lane values 0..127, 64 = 0; a chord lane: semitones from the step's note, 64 = no note
// extra[]: bits 0-2 probability (0 = always, n = 8 - n in 8), bits 3-4 ratchets - 1
#define SEQ_PROBABILITY(x) (8 - ((x) & 7))
#define SEQ_RATCHETS(x)    (((x) >> 3 & 3) + 1)

enum { ARP_OFF, ARP_UP, ARP_DOWN, ARP_UP_DOWN, ARP_RANDOM, ARP_PLAYED, ARP_COUNT };

typedef struct {
    uint8_t length;   // 1..SEQ_MAX_STEPS
    uint8_t lanes;    // 0..SEQ_LANES
    uint8_t division; // step length: an LFO clock division (lfo.h)
    uint8_t bpm;      // without the clock
    uint8_t flags;
    uint8_t swing;    // 0..75 %: even steps longer, odd shorter
    uint8_t smooth;   // bit l: lane l glides from step to step
    uint8_t arp;      // ARP_*: off = the key transposes the steps
    uint8_t note[SEQ_MAX_STEPS];     // offset + 64, in semitones
    uint8_t velocity[SEQ_MAX_STEPS]; // 0 = the key's
    uint8_t gate[SEQ_MAX_STEPS];     // bit 7 on, bits 0-6 percent of the step (SEQ_GATE_TIE ties)
    uint8_t extra[SEQ_MAX_STEPS];    // probability and ratchets (SEQ_PROBABILITY, SEQ_RATCHETS)
    uint8_t lane[SEQ_LANES][SEQ_MAX_STEPS];
} sequence_t;

extern sequence_t sequence;

int seq_max_steps(int lanes); // what fits in SEQ_BYTES
void seq_clear(void);         // empty and off (the init sound)
void seq_fix(void);           // clamps the header to what fits

// MIDI keys: the layers the sequence took the key for (the caller plays or releases it on the rest; 0: none).
// `layers` is where the key would go (synth_key_layers()) for a key-on, and what a key-off may release
// (synth_channel_layers()). A sequence on one layer's keys takes only that layer: in stack mode the other layer
// plays the key itself.
int seq_key_on(uint8_t note, uint8_t velocity, int layers);
int seq_key_off(uint8_t note, int layers);
void seq_stop(void); // releases what's sounding
void seq_update(float dt);
float seq_lane(int lane); // -1..1 while playing, else 0

// Step recording (Rec + Seq 1 on the panel, 0.9.0): start stops the sequence; then every note writes the next step,
// from step 1: its offset from the first note recorded, its velocity, a 50 % gate, always, no ratchets. A rest writes
// a step that's off. The length follows the steps written and the sequence is switched on; the lanes and the
// settings stay. Both return the steps written so far, or -1 when the sequence is full (nothing written).
void seq_record_start(void);
int seq_record_note(uint8_t note, uint8_t velocity);
int seq_record_rest(void);

#endif
