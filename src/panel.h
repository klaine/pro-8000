// Front panel: HT16K33 LED/7-segment driver and key scanner on I2C1 (PB6 = SCL, PB7 = SDA).
#ifndef PANEL_H
#define PANEL_H

#include <stdint.h>

#define PANEL_RAM_BYTES 16 // 8 COM lines × 16 rows
#define PANEL_KEY_BYTES 6

void panel_gpio_init(void);
// Probes 0x70-0x77 and sets the driver up. Returns the address found, or 0.
uint8_t panel_init(void);
void panel_write(const uint8_t ram[PANEL_RAM_BYTES]);
// Key RAM: one bit per key, set while held. Returns 0 if the driver didn't answer.
int panel_read_keys(uint8_t keys[PANEL_KEY_BYTES]);
// The key-interrupt flag: 1 while the latest key scan saw a key down (it clears when the key RAM is read),
// 0 if not, -1 if the driver didn't answer. One byte instead of six.
int panel_key_flag(void);

#endif
