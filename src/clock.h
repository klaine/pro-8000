// System clock and microsecond timing.
#ifndef CLOCK_H
#define CLOCK_H

#include <stdint.h>

// Core clock in Hz: 168 MHz from the 8 MHz crystal through the PLL (which also gives USB its 48 MHz),
// or 16 MHz on the internal oscillator if the crystal doesn't start.
extern uint32_t system_hz;

// Starts the crystal and PLL, and the cycle counter delay_us() uses. Returns 1 if the PLL is running.
int clock_init(void);
void delay_us(uint32_t us);

#endif
