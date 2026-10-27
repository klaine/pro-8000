// Autotune: measures every oscillator and filter through TUNE_CPR and keeps the results in flash.
#ifndef TUNING_H
#define TUNING_H

#include <stdint.h>

#define TUNING_POINTS 14 // CVs 0x2000, 0x3000 ... 0xF000
#define TUNING_ROWS   24 // osc A 1-8, osc B 1-8, filter 1-8
#define TUNING_ROW_OSC_A  0
#define TUNING_ROW_OSC_B  8
#define TUNING_ROW_FILTER 16

uint16_t tuning_point_cv(int point);

// Loads the tables from flash. Returns 1 if a valid set was found.
int tuning_load(void);
int tuning_valid(void);
// Measures everything (about 25 s, silent: master volume at 0), then saves. Leaves the CVs and gates as the
// tuner set them; the caller restores its own. Returns the number of rows with at least two good points.
int tuning_run(void);
// Measured frequency in centihertz for a row and point, or 0 if that point couldn't be measured.
uint32_t tuning_centihertz(int row, int point);
// CV for a MIDI note on a row, from the tables (or from the nominal curve when untuned).
uint16_t tuning_note_cv(int row, float note);

#endif
