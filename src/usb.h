// USB MIDI device on the STM32F446's OTG FS core (PA11 = DM, PA12 = DP), one cable in each direction.
#ifndef USB_H
#define USB_H

#include <stdint.h>

// Needs the 48 MHz clock from the PLL. Enumerates from the OTG FS interrupt.
void usb_init(void);
// Pops one 4-byte USB MIDI event packet received from the host. Returns 0 if none is waiting. When the ring is too full
// for another USB packet the endpoint NAKs and the host queues, so a burst loses nothing; the read that makes room
// opens it again.
int usb_midi_read(uint8_t packet[4]);
// Drops every packet waiting, and opens the endpoint if it was NAKing.
void usb_midi_flush(void);
// For a stall too long for the host to wait (an autotune): while on, the endpoint keeps accepting and a full ring drops,
// so the host isn't held up, realtime bytes still arrive and nothing stale piles up behind the NAKs. Flush before
// turning it off: a nearly full ring has no room for the packet the endpoint is already armed for.
void usb_midi_discard(int on);
// Realtime bytes (F8-FF, data 0) and Song Position Pointer (F2, data = its 14 bits) skip the ring: the interrupt hands
// each to `handler` with the cycle counter at arrival, so clock ticks keep their timing and none is lost while the
// main loop is busy. They share the endpoint, so while it NAKs they wait with the rest: until 16 reads have made room, and
// then behind whatever the host queued ahead of them (usb.c says how long that can be). Set it before usb_init().
void usb_midi_realtime(void (*handler)(uint8_t status, uint16_t data, uint32_t cycles));
// Sends USB MIDI event packets to the host (at most 16 at a time). Returns 0 if not configured or still busy.
int usb_midi_write(const uint8_t *packets, int count);
// Drops off the bus, so the host sees the device unplugged.
void usb_disconnect(void);

#endif
