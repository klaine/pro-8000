// MIDI out (0.10.0): what the synth sends besides its SysEx replies, to USB and the 5-pin socket (din.c, in OUT and
// MERGE), never waiting. midiout.c has the design.
#ifndef MIDIOUT_H
#define MIDIOUT_H

#include <stdint.h>

enum { MIDIOUT_USB = 1, MIDIOUT_DIN = 2 };

// A note on or off (8n, 9n; 9n at velocity 0 is an off) to these ports. An off always gets out in the end; an on may be
// dropped (a full queue, a USB host that doesn't read), and then its off isn't sent either.
void midiout_note(int ports, uint8_t status, uint8_t note, uint8_t velocity);
// A controller: the latest value of a channel's CC wins, so a sweep never queues up
void midiout_cc(int ports, uint8_t channel, uint8_t cc, uint8_t value);
// Another message that must go out whole (program change: 2 bytes)
void midiout_message(int ports, const uint8_t *message, int n);
// Clock: F8 counted, FA / FB / FC the latest
void midiout_realtime(int ports, uint8_t byte);
// The MIDI clock the synth follows on the socket, forwarded to USB: from the socket's interrupt (F8, FA, FB, FC)
void midiout_forward_usb(uint8_t byte);
// Note Offs for every note sent on and not off yet, and the notes still queued dropped (the panic, Tune: main.c's
// notes_out_off())
void midiout_all_notes_off(int ports);
// Hands what's queued to the ports as each can take it; from the main loop, as often as handle_midi()
void midiout_poll(uint32_t now_ms);
// Whether a USB host has stopped reading (nothing went through for MIDIOUT_STALL_MS with something waiting)
int midiout_usb_stalled(void);
// Whether anything waits for these ports (queued, owed, controllers; USB's clock): the drains before Tune and update
// mode, and the socket's switch to THRU, wait for none (din_tx_idle() for the socket's own ring)
int midiout_pending(int ports);
// After the main loop stood still (a tune): USB's stall time starts again, and what the socket's clock brought
// meanwhile isn't forwarded as a burst
void midiout_resume(uint32_t now_ms);
#define MIDIOUT_STALL_MS 100

#endif
