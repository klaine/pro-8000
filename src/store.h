// Our layout of the patch EEPROM (the stock image in backup/ is the way back to stock firmware):
//   0x00000-0x0C7FF  200 patches (A00-b99), 256 bytes each, in the same slots as the stock firmware's
//   0x0C800-0x18FFF  200 sequences, 256 bytes each: patch n's at 0x0C800 + 256 n (from 0.7.0; stock banks C and D)
//   0x19000-0x1F3FF  the stock factory bank, left as it is
//   0x1F400          global block: last patch, preset or manual mode
//   0x1F500-0x1FBFF  the autotune tables (tuning.c)
#ifndef STORE_H
#define STORE_H

#include <stdint.h>
#include "controls.h"
#include "mod.h"

#define PATCH_COUNT 200
#define PATCH_NAME  16
#define FACTORY_COUNT 100
#define STORE_TUNING_ADDRESS 0x1F500u
#define STORE_TUNING_SIZE    0x700u

typedef struct {
    char name[PATCH_NAME + 1];
    uint8_t layer_mode, split;
    uint8_t lfo_sync[2], lfo_trigger[2]; // LFO 1, LFO 2
    uint8_t lfo_shape[2][2];             // [layer][LFO 1, LFO 2], lfo_shape_t
    uint8_t lfo2_dests[2];               // per layer, LFO2_TO_* bits
    uint8_t env_cycle[2];                // per layer, ENV_CYCLE_* bits
    uint8_t bend_range;                  // 1-12 semitones; 0 = not stored (2)
    uint8_t detune;                      // unison detune cents + 1; 0 = not stored (14)
    uint8_t env_sync[2][4];              // [layer][amp, filter, Env 3, Env 4]: 0 free, else clock division + 1
    uint16_t step_pattern[2][2];         // [layer][LFO 1, LFO 2]: bit n = step n on
    uint8_t step_count[2][2];            // 1..16; 0 = not stored (before 0.6.3): the default pattern
    uint32_t switches; // bit s: switch s engaged (controls_on() sense), for the latching buttons
    uint8_t keytrack;  // 0 off, 1 half, 2 full
    uint16_t pots[2][POT_COUNT]; // per layer, 0..65535; shared pots are the same in both
    uint16_t extra[2][EXTRA_PARAMS]; // Env 3, Env 4, LFO 2 rate and amount per layer
    route_t routes[2][ROUTES];
} patch_t;

typedef enum { SLOT_EMPTY, SLOT_OURS, SLOT_STOCK, SLOT_CORRUPT } slot_kind_t;

void store_init(void);
// The EEPROM carries our global block (it has been imported or formatted).
int store_ready(void);
slot_kind_t store_slot_kind(int n);
int store_load_patch(int n, patch_t *patch); // 1 if slot n holds one of our patches
int store_save_patch(int n, const patch_t *patch);
// Converts slot n from the stock format in place. 1 converted, 0 nothing to do (empty or already ours), -1 error.
int store_import_stock(int n);
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
