// Parameters beyond the panel's knobs, and the mod matrix types (shared by the engine and the patch format).
#ifndef MOD_H
#define MOD_H

#include <stdint.h>
#include "controls.h"

// Env 3 and Env 4: edited with the filter ADSR knobs when the keyboard-tracking switch is in its middle / top
// position. Parameters 0..POT_COUNT-1 are the knobs themselves.
enum {
    PARAM_ENV3_ATT = POT_COUNT, PARAM_ENV3_DEC, PARAM_ENV3_SUS, PARAM_ENV3_REL,
    PARAM_ENV4_ATT, PARAM_ENV4_DEC, PARAM_ENV4_SUS, PARAM_ENV4_REL,
    PARAM_COUNT
};
#define EXTRA_PARAMS (PARAM_COUNT - POT_COUNT)

// Sources, numbered as MOD ASSIGN picks them with the keypad: 1-9, and 0 for SRC_NOTE.
typedef enum {
    SRC_NONE, SRC_LFO, SRC_FILTER_ENV, SRC_AMP_ENV, SRC_VELOCITY, SRC_MOD_WHEEL, SRC_AFTERTOUCH, SRC_ENV3, SRC_ENV4,
    SRC_RANDOM, SRC_NOTE, SRC_COUNT
} mod_source_t;

// Destinations, picked by touching a knob or an LFO routing button. Up to DEST_VCA they're per voice; from
// DEST_A_LEVEL on they're single wires on the hardware, set from the layer's most recently played voice.
typedef enum {
    DEST_PITCH_A, DEST_PITCH_B, DEST_PITCH, DEST_CUTOFF, DEST_FIL_ENV_AMT, DEST_PMOD_ENV, DEST_LFO_AMOUNT,
    DEST_LFO_TO_PITCH, DEST_LFO_TO_FILTER, DEST_VCA,
    DEST_A_LEVEL, DEST_B_LEVEL, DEST_NOISE, DEST_RESONANCE, DEST_A_PW, DEST_B_PW, DEST_PMOD_OSC_B, DEST_LFO_TO_PW,
    DEST_COUNT
} mod_dest_t;
#define DEST_NONE 0xFF

#define ROUTES 8 // per layer

typedef struct {
    uint8_t source; // SRC_NONE = unused
    uint8_t dest;
    int8_t depth;   // -99..99
} route_t;

#endif
