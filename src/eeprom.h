// The patch memory: U11, a 25M01 (1 Mbit = 128 kB SPI EEPROM, 24-bit addresses, 256-byte pages), on PE1-PE6.
#ifndef EEPROM_H
#define EEPROM_H

#include <stdint.h>

#define EEPROM_SIZE (128u * 1024u)
#define EEPROM_PAGE 256u

void eeprom_init(void);
void eeprom_read(uint32_t address, uint8_t *data, uint32_t length);
uint8_t eeprom_status(void);
// Writes any range, split at page boundaries; each page takes up to 5 ms. Returns 0 if the chip stayed busy.
int eeprom_write(uint32_t address, const uint8_t *data, uint32_t length);

#endif
