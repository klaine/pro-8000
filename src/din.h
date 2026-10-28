// 5-pin DIN MIDI in, on UART4 (PA1). Channel messages and the realtime clock; SysEx stays on USB.
#ifndef DIN_H
#define DIN_H

#include <stdint.h>

// Starts receiving. Realtime bytes (F8-FF, data 0) and Song Position Pointer (F2, data = its 14 bits) skip the queue:
// the UART interrupt hands each to `handler` with the cycle counter at arrival, as usb_midi_realtime() does, so give
// both the same handler.
void din_init(void (*handler)(uint8_t status, uint16_t data, uint32_t cycles));
// Pops the next complete channel message as a USB MIDI event packet (cable 0), so one parser handles both inputs.
// Returns 0 if none is waiting.
int din_midi_read(uint8_t packet[4]);
// Drops every byte waiting (after a long stall, such as tuning).
void din_midi_flush(void);

#endif
