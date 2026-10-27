// Our layout of the patch EEPROM (the stock image in backup/ is the way back to stock firmware):
//   0x00000-0x18FFF  400 patches (A00-D99), 256 bytes each, in the same slots as the stock firmware's
//   0x19000-0x1F3FF  the stock factory bank, left as it is
//   0x1F400          global block: last patch, preset or manual mode
//   0x1F500-0x1FBFF  the autotune tables (tuning.c)
#ifndef STORE_H
#define STORE_H

#include <stdint.h>
#include "controls.h"

#define PATCH_COUNT 400
#define PATCH_NAME  16
#define STORE_TUNING_ADDRESS 0x1F500u
#define STORE_TUNING_SIZE    0x700u

typedef struct {
    char name[PATCH_NAME + 1];
    uint8_t layer_mode, split, lfo_sync, lfo_trigger;
    uint32_t switches; // bit s: switch s engaged (controls_on() sense), for the latching buttons
    uint8_t keytrack;  // 0 off, 1 half, 2 full
    uint16_t pots[2][POT_COUNT]; // per layer, 0..65535; shared pots are the same in both
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
// Writes the global block, marking the EEPROM as ours.
int store_finish_import(void);
int store_last_patch(void);
int store_preset_mode(void);
void store_set_last(int patch, int preset_mode); // written only when it changed

#endif
