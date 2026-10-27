// Front-panel inputs: pots (three 74HC4051s plus two direct), switches on GPIO, the HT16K33's key scan and the
// VALUE encoder. Indices are hardware positions; which control sits where is mapped in NOTES.md.
#ifndef CONTROLS_H
#define CONTROLS_H

#include <stdint.h>

#define POT_COUNT    26 // 3 muxes × 8 (index = mux × 8 + address), then CUTOFF (24) and NOISE (25)
#define SWITCH_COUNT 24 // see switch_pins in controls.c
#define KEY_BYTES    6  // HT16K33 key RAM

// Pot indices as mapped on the panel (2026-10-27): mux order runs opposite to the schematic's drawing.
typedef enum {
    POT_AMP_REL, POT_AMP_SUS, POT_AMP_DEC, POT_AMP_ATT, POT_FIL_REL, POT_FIL_SUS, POT_FIL_DEC, POT_FIL_ATT, // U22
    POT_RESONANCE, POT_FIL_ENV_AMT, POT_FREQ_A, POT_FREQ_B, POT_FINE, POT_A_PW, POT_B_PW, POT_A_LEVEL,      // U21
    POT_GLIDE, POT_LFO_FREQ /* also the value knob: no encoder is fitted */, POT_LFO_AMOUNT, POT_MASTER_TUNE, POT_MASTER_VOL, POT_B_LEVEL, POT_PMOD_OSC_B,
    POT_PMOD_FIL_ENV,                                                                                       // U42
    POT_CUTOFF, POT_NOISE,
} pot_t;

// Switch inputs (the order of switch_pins in controls.c), mapped on the panel 2026-10-27. The latching buttons
// read 1 when pressed in. Keyboard tracking is a 3-position switch on two active-low lines: Off leaves both at 1,
// Half pulls KEYTRACK_HALF to 0, Full pulls KEYTRACK_FULL to 0 (see controls_keytrack()).
typedef enum {
    SWITCH_A_SAW, SWITCH_A_TRI, SWITCH_A_SQUARE, SWITCH_B_SAW, SWITCH_B_TRI, SWITCH_B_SQUARE,
    SWITCH_LFO_PW,      // net PW_A_B
    SWITCH_LFO_FREQ,    // net F_A_B
    SWITCH_PMOD_FREQ_A, // net FREQ_A
    SWITCH_LFO_SHAPE, SWITCH_LFO_FILTER,
    SWITCH_PMOD_FILTER, // net FILTER
    SWITCH_UNISON, SWITCH_KEYTRACK_HALF, SWITCH_KEYTRACK_FULL,
    SWITCH_SYNC,        // net SYNC_SEL: oscillator A sync
    // not on the panel's buttons, and not seen changing yet:
    SWITCH_MIDI_CH_0, SWITCH_MIDI_CH_1, SWITCH_MIDI_CH_2, SWITCH_MIDI_CH_3, SWITCH_BACKPANEL_PRESENCE,
    SWITCH_PEDAL_DETECT, SWITCH_SYNC_IN_A, SWITCH_SYNC_IN_B,
} switch_t;

// Buttons on the HT16K33 key scan, as mapped on the panel.
typedef enum {
    BUTTON_0, BUTTON_1, BUTTON_2, BUTTON_3, BUTTON_4, BUTTON_5, BUTTON_6, BUTTON_7, BUTTON_8, BUTTON_9,
    BUTTON_PRESET, BUTTON_PERF, BUTTON_SETTINGS, BUTTON_TUNE, BUTTON_SYNC_SOURCE, BUTTON_SYNC_CLOCK, BUTTON_REC,
    BUTTON_SEQ1, BUTTON_SEQ2, BUTTON_ARP_UP, BUTTON_ARP_ASSIGN, BUTTON_COUNT
} button_t;

typedef enum { CONTROL_POT, CONTROL_SWITCH, CONTROL_KEYS, CONTROL_ENCODER, CONTROL_BUTTON } control_kind_t;

// index: pot / switch / key-RAM byte / button; value: 12-bit pot, 0/1 switch, key byte, encoder transitions (±1),
// 1/0 button pressed/released
typedef void (*control_event_t)(control_kind_t kind, int index, int32_t value);

void controls_init(void);
// Non-blocking: call every main-loop pass. Reports changes through `event` (may be 0).
void controls_poll(uint32_t now_ms, control_event_t event);
uint16_t controls_pot(int index);
int controls_switch(int index);
int controls_button(button_t button);
int controls_keytrack(void); // 0 off, 1 half, 2 full

#endif
