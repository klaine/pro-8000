// The patch memory: U11, a 25M01 (1 Mbit = 128 kB SPI EEPROM, 24-bit addresses), on PE1-PE6.
// Read-only so far: the stock firmware's patches live here, and nothing writes it until our format is settled.
#ifndef EEPROM_H
#define EEPROM_H

#include <stdint.h>

#define EEPROM_SIZE (128u * 1024u)

void eeprom_init(void);
void eeprom_read(uint32_t address, uint8_t *data, uint32_t length);
uint8_t eeprom_status(void);

#endif
