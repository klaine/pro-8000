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

// Out (0.10.0). The THRU socket gives the IN socket's bytes (DIN_THRU, the stock default and the start), the synth's
// own (DIN_OUT), or both, the IN socket's forwarded whole messages at a time between the synth's (DIN_MERGE).
enum { DIN_THRU, DIN_OUT, DIN_MERGE };
void din_set_socket(int mode);
int din_socket(void);
// Queues a whole message (with its status) to go out; 0 if it doesn't fit now, or in THRU, where nothing of ours goes
// out. Never blocks: the transmit interrupt sends it.
int din_send(const uint8_t *bytes, int n);
int din_tx_room(void); // bytes free
int din_tx_idle(void); // nothing of the synth's own waits (the byte in the shift register aside)
void din_safe(void); // PA0 high from reset (startup.c, main()): THRU stays quiet until the UART drives it
void din_poll(uint32_t now_ms); // from the main loop: gives up a forwarded message the IN socket left unfinished
void din_prepare_thru(void); // before a switch to THRU: the IN stream's running status goes out (wait for din_tx_idle())
void din_realtime(uint8_t byte); // F8 (counted) or FA / FB / FC (the latest), sent before the next queued byte
void din_drop_input_clock(int on); // MERGE: leave the IN socket's clock out while the synth sends its own

#endif
