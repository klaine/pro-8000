// The three DACs feeding the S&H multiplexers:
//   dac16: DAC80501 (U46, 16-bit) → oscillator pitch and filter cutoff
//   dac12: on-chip DAC channel 1 (PA4) → global CVs, channel 2 (PA5) → VCA levels
#ifndef DAC_H
#define DAC_H

#include <stdint.h>

void dac_init(void);
void dac16_write(uint16_t value);
void dac12_write(int channel, uint16_t value); // channel 1 or 2; value is 16-bit, the top 12 bits are used

#endif
