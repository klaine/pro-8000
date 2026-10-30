// Our layout of the patch EEPROM (store.c has the records; the stock image in backup/ is the way back to stock
// firmware):
//   0x00000-0x17FFF  128 patches, 768 bytes each: the patch and its step sequence
//   0x19000-0x1F3FF  the stock factory bank, left as it is
//   0x1F400          global block: format, last patch, preset or manual mode, the CC A / B source numbers
//   0x1F500-0x1FBFF  the autotune tables (tuning.c)
#ifndef STORE_H
#define STORE_H

#include <stdint.h>
#include "controls.h"
#include "mod.h"

#define PATCH_COUNT 128
#define PATCH_NAME  16
#define FACTORY_COUNT 100
#define STORE_TUNING_ADDRESS 0x1F500u
#define STORE_TUNING_SIZE    0x700u

typedef struct {
    char name[PATCH_NAME + 1];
    uint8_t layer_mode, split;
    uint8_t layer_voices;                // layer 1's voices in the two-layer modes, 1-7; 0 = automatic (0.9.0)
    uint8_t osc_tune[2];                  // per layer: oscillator A mode | oscillator B mode << 2
    uint8_t lfo_sync[2], lfo_trigger[2]; // LFO 1, LFO 2
    uint8_t lfo_shape[2][2];             // [layer][LFO 1, LFO 2], lfo_shape_t
    uint8_t lfo2_dests[2];               // per layer, LFO2_TO_* bits
    uint8_t env_cycle[2];                // per layer, ENV_CYCLE_* bits
    uint8_t bend_range;                  // 1-12 semitones; 0 = not stored (2)
    uint8_t detune;                      // unison detune cents + 1; 0 = not stored (14)
    uint8_t env_sync[2][4];              // [layer][amp, filter, Env 3, Env 4]: 0 free, else clock division + 1
    uint16_t step_pattern[2][2];         // [layer][LFO 1, LFO 2]: bit n = step n on
    uint8_t step_count[2][2];            // 1..16; 0 = not stored (before 0.6.3): the default pattern
    uint16_t switches; // bit s: switch s engaged (controls_on() sense), for the latching buttons
    uint8_t drift;     // 0-99
    uint8_t morph;     // the morph target patch + 1; 0 = none
    uint8_t scale;     // scale | root << 4
    uint8_t voice_mode; // layer 1 mode | priority << 2, layer 2 the same << 4
    uint8_t keytrack;  // 0 off, 1 half, 2 full
    uint16_t pots[2][POT_COUNT]; // per layer, 0..65535; shared pots are the same in both
    uint16_t extra[2][EXTRA_PARAMS]; // Env 3, Env 4, LFO 2 rate and amount per layer
    route_t routes[2][ROUTES];
    uint8_t lfo_delay[2], lfo_fade[2]; // per LFO, 0-99 (lfo.h); 0 in patches from before 0.7.9
    int8_t macro_offset[2][2];         // [layer][macro], -99..99 (0.8.1)
    uint8_t env_trigger[2], env_dahdsr[2]; // per layer, a bit per envelope (0.9.0)
    uint8_t env_curve[2];                  // per layer, 2 bits per envelope: ENV_CURVE_* (0.9.0)
    uint8_t env_delay[2][4], env_hold[2][4]; // [layer][amp, filter, Env 3, Env 4], 0-99 (0.9.0)
    uint8_t env_count[2][4];                 // [layer][envelope] cycles, 0 = endless (0.9.1)
    uint8_t env_tempo[2];                    // per layer, a bit per envelope: stage times in note values (0.9.1)
    uint8_t layer_link;                      // LINK_* bits: what layer 2 takes from layer 1 (0.9.3)
    uint8_t lfo_cycles[2];                   // per LFO, a one shot's cycles; 0 (older patches) = 1 (0.9.3)
} patch_t;

typedef enum { SLOT_EMPTY, SLOT_OURS, SLOT_STOCK, SLOT_CORRUPT } slot_kind_t; // (SLOT_STOCK: no longer in slots)

void store_init(void);
// The EEPROM carries our global block (it has been imported or formatted).
int store_ready(void);
slot_kind_t store_slot_kind(int n);
int store_load_patch(int n, patch_t *patch); // 1 if slot n holds one of our patches
int store_save_patch(int n, const patch_t *patch);
// Wipes every patch (all slots empty) and writes the global block: the EEPROM becomes ours, in this format.
int store_format(void);
int store_formatted(void); // 1 if store_init() just did that (an older format of ours was found)
// The CC A / CC B mod source numbers (0 = the default), kept in the global block
int store_cc_number(int which);
void store_set_cc_number(int which, int number);
// The MIDI channel the synth listens on (1-16, 0 = every channel), kept in the global block
int store_midi_channel(void);
void store_set_midi_channel(int channel);
// Knob pickup (1, the default) or jump (0: a knob that moves takes over at once), kept in the global block
int store_pickup(void);
void store_set_pickup(int on);
// The clock source (0 MIDI, 1 internal, 2 SYNC IN) and the internal tempo in tenths of a BPM, kept in the global block
int store_clock_source(void);
int store_bpm_tenths(void);
void store_set_clock(int source, int bpm_tenths);
// The stock factory bank (100 patches, left in place at 0x19000): its name, or 0 if that slot isn't a stock patch.
int store_factory_name(int f, char name[PATCH_NAME + 1]);
// Converts factory patch f into slot n. Returns 0 if f isn't a stock patch, -1 on a write error, else 1.
int store_import_factory(int f, int n);
// Sets the name of slot n (one of ours). Returns 0 if the slot isn't ours or the write failed.
int store_rename(int n, const char *name);
int store_clear(int n); // the slot becomes empty (all zeros), its sequence too
// Patch n's step sequence: loading gives 0 if it has none (the caller clears it); saving writes `sequence`.
int store_load_sequence(int n);
int store_save_sequence(int n);
// Writes the global block, marking the EEPROM as ours.
int store_finish_import(void);
int store_last_patch(void);
int store_preset_mode(void);
void store_set_last(int patch, int preset_mode); // written only when it changed

#endif
