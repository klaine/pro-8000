// The three DACs feeding the S&H multiplexers:
//   dac16: DAC80501 (U46, 16-bit) → oscillator pitch and filter cutoff
//   dac12: on-chip DAC channel 1 (PA4) → global CVs, channel 2 (PA5) → VCA levels
#ifndef DAC_H
#define DAC_H

#include <stdint.h>

typedef enum { DAC_BUS_BITBANG, DAC_BUS_SPI } dac_bus_t;

// How the DAC80501 is driven. Bit-banged: `high` / `low` are SCLK's high and low times in core cycles, `mode` 0
// changes SDIN as SCLK rises, 1 just after it falls. Hardware SPI3: `high` is the baud-rate divider (APB1 / 2^(n+1),
// 0-7), `mode` the SPI mode (bit 0 CPHA, bit 1 CPOL). `speed` is the pins' GPIO speed (0 low ... 3 very high),
// `sync_us` the wait after SYNC falls and after it rises.
typedef struct {
    uint8_t bus, mode;
    uint16_t high, low;
    uint8_t speed, sync_us;
} dac_config_t;

extern const dac_config_t dac_default;

void dac_init(void);
// Switches the bus and sets the DAC up again through it (a probe: not stored). Returns 0 for a config out of range.
int dac_configure(const dac_config_t *config);
const dac_config_t *dac_config(void);
// Writes the DAC80501. Its output only changes as the frame ends, so the S&H channel it feeds can keep sampling while
// the next frame clocks in: `before_update` (may be 0) runs just before the last two bits, to end that sample. On the
// SPI probe bus it runs before the frame.
void dac16_write(uint16_t value, void (*before_update)(void));
// Sends the DAC80501's config and gain registers again (as at start-up); nothing may sample it meanwhile.
void dac16_setup(void);
// Writes any DAC80501 register (a probe; nothing may sample it meanwhile).
void dac16_register(uint8_t command, uint16_t value);
void dac12_write(int channel, uint16_t value); // channel 1 or 2; value is 16-bit, the top 12 bits are used

#endif
