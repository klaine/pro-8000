// USB MIDI device on the STM32F446's OTG FS core (PA11 = DM, PA12 = DP), one cable in each direction.
#ifndef USB_H
#define USB_H

#include <stdint.h>

// Needs the 48 MHz clock from the PLL. Enumerates from the OTG FS interrupt.
void usb_init(void);
// Pops one 4-byte USB MIDI event packet received from the host. Returns 0 if none is waiting.
int usb_midi_read(uint8_t packet[4]);
// Sends USB MIDI event packets to the host (at most 16 at a time). Returns 0 if not configured or still busy.
int usb_midi_write(const uint8_t *packets, int count);
// Drops off the bus, so the host sees the device unplugged.
void usb_disconnect(void);

#endif
