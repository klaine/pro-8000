// Frequency measurement through the tuning comparator: CAL_HOOK from the voice board → LM311 (U34A) → TUNE_CPR,
// which reaches the MCU on PB8.
#ifndef TUNE_H
#define TUNE_H

#include <stdint.h>

#define TUNE_MAX_PERIODS 255

typedef struct {
    uint32_t cycles;  // span of the counted periods at system_hz; 0 = no usable signal
    uint32_t periods; // whole periods in that span
} tune_result_t;

void tune_init(void);
// Timestamps up to max_periods + 1 edges of TUNE_CPR (falling edges unless `rising`), refreshing the S&H
// meanwhile, until timeout_ms. The comparator sometimes adds stray edges, so the period is the median interval,
// and the span runs between the first and last edges that border a median-length interval.
tune_result_t tune_measure(uint32_t max_periods, uint32_t timeout_ms, int rising);

#endif
