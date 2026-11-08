// MIDI out (0.10.0). Notes, controllers, program changes and clock go to USB and to the 5-pin socket, and nothing here
// ever waits. Per port: a FIFO of whole messages (notes first among them as they came), controller slots where a CC's
// latest value replaces the one waiting (a knob sweep or a CC-out route never queues up), clock counted. midiout_poll()
// hands them on as each port can take them: the socket's ring (din.c, drained by its interrupt at 31250 baud), USB a
// transfer of up to 16 packets whenever the endpoint is free (usb_midi_write() answers at once).
//
// Notes can't be lost one way: per port and channel a bit per note says "on sent, off not yet", and an off that finds
// the queue full is owed (a second bitmap) and goes out before anything else. A USB host that stops reading (Windows with
// no program on the port takes nothing) would fill the queue for good, so after MIDIOUT_STALL_MS with nothing going
// through the queue is dropped: every note sent on becomes an owed off, which is all that's tried until a transfer goes
// through again, or (nothing owed) until the endpoint is free again (usb_midi_ready()). An off that finds the queue full
// takes its own on with it if that's still queued, so a note never goes out with its off spent before it.
#include "midiout.h"
#include "din.h"
#include "usb.h"

#define PORTS 2
#define FIFO 64           // whole messages of 1-3 bytes
#define ROOM_FOR_OFFS 16  // an on takes a place only while this many are left for the offs
#define SLOTS 16          // controllers waiting

typedef struct {
    uint8_t m[3], n;
} message_t;

typedef struct {
    uint8_t channel, cc, value, used;
} slot_t;

static struct {
    message_t fifo[FIFO];
    uint8_t head, tail;
    slot_t slot[SLOTS];
    uint8_t clock, start_stop, ss_clocks; // F8s owed, the latest FA / FB / FC, the F8s owed from before it
    uint16_t on[16][8], owed[16][8];      // per channel a bit per note: sent on and not off; an off owed
} port[PORTS];
static uint32_t usb_ok_at;               // ms of the last transfer that went through (or of the last with nothing waiting)
// From the socket's interrupt, which alone writes these: F8s counted, the latest FA / FB / FC, a count of those and
// the F8 count as it came
static volatile uint8_t forwarded_clocks, forwarded_start_stop, forwarded_start_stops, forwarded_start_stop_clocks;
static uint8_t forwarded_seen, forwarded_start_stops_seen; // the counts as the main loop last took them
static int usb_stalled, polled;

static int fifo_used(int p) { return (port[p].head - port[p].tail + FIFO) % FIFO; }

static int is_on(const uint8_t *m, int n) { return n == 3 && (m[0] & 0xF0) == 0x90 && m[2]; }
static int is_off(const uint8_t *m, int n) { return n == 3 && ((m[0] & 0xF0) == 0x80 || ((m[0] & 0xF0) == 0x90 && !m[2])); }

static void bit_set(uint16_t b[16][8], int channel, int note, int on)
{
    uint16_t mask = (uint16_t)(1u << (note & 15));
    if (on)
        b[channel & 15][note >> 4 & 7] |= mask;
    else
        b[channel & 15][note >> 4 & 7] &= (uint16_t)~mask;
}

static int bit_get(uint16_t b[16][8], int channel, int note) { return b[channel & 15][note >> 4 & 7] >> (note & 15) & 1; }

// An off with no room: the ons of its note queued since the note's last queued off go with it (marked n = 0, which
// the ports skip), as they would otherwise go out after the owed off and hang (a source may send two ons for a note)
static void cancel_queued_on(int p, const uint8_t *m)
{
    for (int i = port[p].head; i != port[p].tail;) {
        i = (i + FIFO - 1) % FIFO;
        message_t *e = &port[p].fifo[i];
        if (e->n != 3 || (e->m[0] & 0x0F) != (m[0] & 0x0F) || e->m[1] != m[1])
            continue;
        if (is_off(e->m, 3))
            return; // the ons before it are that off's
        if (is_on(e->m, 3))
            e->n = 0;
    }
}

static void push(int p, const uint8_t *m, int n)
{
    if (is_off(m, n) && fifo_used(p) >= FIFO - 1) { // no room: owed, if its on went out (else it never sounded)
        cancel_queued_on(p, m);
        if (bit_get(port[p].on, m[0], m[1]))
            bit_set(port[p].owed, m[0], m[1], 1);
        return;
    }
    if (fifo_used(p) >= FIFO - 1 - (is_on(m, n) ? ROOM_FOR_OFFS : 0))
        return; // an on, a program change: dropped
    message_t *e = &port[p].fifo[port[p].head];
    for (int i = 0; i < n; i++)
        e->m[i] = m[i];
    e->n = (uint8_t)n;
    port[p].head = (uint8_t)((port[p].head + 1) % FIFO);
}

static int port_index(int bit) { return bit == MIDIOUT_USB ? 0 : 1; }

void midiout_note(int ports, uint8_t status, uint8_t note, uint8_t velocity)
{
    uint8_t m[3] = {status, (uint8_t)(note & 0x7F), (uint8_t)(velocity & 0x7F)};
    for (int bit = 1; bit <= 2; bit <<= 1) {
        if (!(ports & bit))
            continue;
        if (bit == MIDIOUT_USB && usb_stalled) { // an on is dropped; an off is owed already if its on went out
            if (is_off(m, 3) && bit_get(port[0].on, m[0], m[1]))
                bit_set(port[0].owed, m[0], m[1], 1);
            continue;
        }
        push(port_index(bit), m, 3);
    }
}

void midiout_message(int ports, const uint8_t *message, int n)
{
    for (int bit = 1; bit <= 2; bit <<= 1)
        if (ports & bit && n >= 1 && n <= 3 && !(bit == MIDIOUT_USB && usb_stalled)) // (a stalled USB: dropped)
            push(port_index(bit), message, n);
}

void midiout_cc(int ports, uint8_t channel, uint8_t cc, uint8_t value)
{
    for (int bit = 1; bit <= 2; bit <<= 1) {
        if (!(ports & bit) || (bit == MIDIOUT_USB && usb_stalled))
            continue;
        int p = port_index(bit), free_slot = -1;
        for (int i = 0; i < SLOTS; i++) {
            if (port[p].slot[i].used && port[p].slot[i].channel == channel && port[p].slot[i].cc == cc) {
                port[p].slot[i].value = value; // the latest wins
                free_slot = -2;
                break;
            }
            if (!port[p].slot[i].used && free_slot == -1)
                free_slot = i;
        }
        if (free_slot >= 0)
            port[p].slot[free_slot] = (slot_t){(uint8_t)(channel & 15), (uint8_t)(cc & 0x7F), (uint8_t)(value & 0x7F), 1};
    }
}

void midiout_realtime(int ports, uint8_t byte)
{
    if (ports & MIDIOUT_DIN)
        din_realtime(byte); // straight into the socket's own realtime slot
    if (ports & MIDIOUT_USB && !usb_stalled) {
        if (byte == 0xF8) {
            if (port[0].clock < 255)
                port[0].clock++;
        } else {
            port[0].start_stop = byte;
            port[0].ss_clocks = port[0].clock;
        }
    }
}

void midiout_all_notes_off(int ports)
{
    for (int bit = 1; bit <= 2; bit <<= 1) {
        if (!(ports & bit))
            continue;
        int p = port_index(bit), kept = 0;
        for (int i = port[p].tail; i != port[p].head; i = (i + 1) % FIFO) // what's queued but notes (and cancelled ons)
            if (port[p].fifo[i].n && !is_on(port[p].fifo[i].m, port[p].fifo[i].n) && !is_off(port[p].fifo[i].m, port[p].fifo[i].n))
                port[p].fifo[(port[p].tail + kept++) % FIFO] = port[p].fifo[i];
        port[p].head = (uint8_t)((port[p].tail + kept) % FIFO);
        for (int c = 0; c < 16; c++)
            for (int k = 0; k < 8; k++)
                port[p].owed[c][k] |= port[p].on[c][k];
    }
}

// The next owed off of a port, as a message; 0 if none
static int next_owed(int p, uint8_t *m)
{
    for (int c = 0; c < 16; c++)
        for (int k = 0; k < 8; k++)
            if (port[p].owed[c][k]) {
                int note = k * 16;
                while (!(port[p].owed[c][k] >> (note & 15) & 1))
                    note++;
                m[0] = (uint8_t)(0x80 | c);
                m[1] = (uint8_t)note;
                m[2] = 64;
                return 1;
            }
    return 0;
}

// A message has gone out on a port: what's sounding there now
static void sent(int p, const uint8_t *m, int n)
{
    if (is_on(m, n))
        bit_set(port[p].on, m[0], m[1], 1);
    else if (is_off(m, n)) {
        bit_set(port[p].on, m[0], m[1], 0);
        bit_set(port[p].owed, m[0], m[1], 0);
    }
}

static void poll_din(void)
{
    uint8_t m[3];
    while (next_owed(1, m) && din_send(m, 3)) // din_send() fails in THRU, with nothing of ours going out
        sent(1, m, 3);
    while (port[1].tail != port[1].head) {
        message_t *e = &port[1].fifo[port[1].tail];
        if (e->n && !din_send(e->m, e->n))
            return;
        sent(1, e->m, e->n);
        port[1].tail = (uint8_t)((port[1].tail + 1) % FIFO);
    }
    for (int i = 0; i < SLOTS; i++)
        if (port[1].slot[i].used) {
            uint8_t c[3] = {(uint8_t)(0xB0 | port[1].slot[i].channel), port[1].slot[i].cc, port[1].slot[i].value};
            if (!din_send(c, 3))
                return;
            port[1].slot[i].used = 0;
        }
}

static void packet(uint8_t *out, const uint8_t *m, int n)
{
    uint8_t status = m[0];
    out[0] = status >= 0xF8 ? 0x0F : status >> 4; // cable 0; realtime is CIN F, a channel message its status' high nibble
    out[1] = status;
    out[2] = n > 1 ? m[1] : 0;
    out[3] = n > 2 ? m[2] : 0;
}

void midiout_forward_usb(uint8_t byte)
{
    if (byte == 0xF8)
        forwarded_clocks++; // a count the main loop reads the change of: no read-modify-write shared with it
    else {
        forwarded_start_stop = byte;
        forwarded_start_stop_clocks = forwarded_clocks;
        __asm volatile("" ::: "memory");
        forwarded_start_stops++; // the same for these
    }
}

static uint8_t clock_add(uint8_t a, int b) { return (uint8_t)(a + b < 255 ? a + b : 255); }

static void poll_usb(uint32_t now)
{
    uint8_t start_stops = forwarded_start_stops;
    __asm volatile("" ::: "memory");
    uint8_t clocks_in = forwarded_clocks, start_stop_in = 0, add = (uint8_t)(clocks_in - forwarded_seen), before = 0;
    if (start_stops != forwarded_start_stops_seen) {
        start_stop_in = forwarded_start_stop;
        before = (uint8_t)(forwarded_start_stop_clocks - forwarded_seen); // the forwarded clocks that came before it
        if (before > add)
            before = add;
    }
    forwarded_seen = clocks_in;
    forwarded_start_stops_seen = start_stops;
    if (!usb_stalled && start_stop_in) {
        port[0].clock = clock_add(port[0].clock, before);
        port[0].start_stop = start_stop_in;
        port[0].ss_clocks = port[0].clock;
        port[0].clock = clock_add(port[0].clock, add - before);
    } else if (!usb_stalled)
        port[0].clock = clock_add(port[0].clock, add);
    uint8_t packets[16 * 4], m[3];
    int count = 0, from_fifo = 0, clocks = 0, start_stop = 0, owed = 0, slots[SLOTS], slot_count = 0;
    uint16_t owed_taken[16][8] = {{0}};
    // owed offs first, then the clock, the queue and the controllers, as many as one transfer takes
    while (count < 16 && next_owed(0, m) && !bit_get(owed_taken, m[0], m[1])) {
        bit_set(owed_taken, m[0], m[1], 1);
        bit_set(port[0].owed, m[0], m[1], 0); // put back below if the transfer doesn't go
        packet(&packets[4 * count++], m, 3);
        owed++;
    }
    for (int c = 0; c < 16; c++) // restore what's taken (sent() clears it once the transfer goes through)
        for (int k = 0; k < 8; k++)
            port[0].owed[c][k] |= owed_taken[c][k];
    // the clock in the order it came: the F8s from before a start / stop, it, then the ones after it
    uint8_t f8 = 0xF8;
    int before_it = port[0].start_stop && port[0].ss_clocks < port[0].clock ? port[0].ss_clocks : port[0].clock;
    while (count < 16 && clocks < before_it) {
        packet(&packets[4 * count++], &f8, 1);
        clocks++;
    }
    if (count < 16 && port[0].start_stop) {
        packet(&packets[4 * count++], &port[0].start_stop, 1);
        start_stop = 1;
        while (count < 16 && clocks < port[0].clock) {
            packet(&packets[4 * count++], &f8, 1);
            clocks++;
        }
    }
    for (int i = port[0].tail; count < 16 && i != port[0].head; i = (i + 1) % FIFO) {
        if (port[0].fifo[i].n) // (a cancelled on: skipped)
            packet(&packets[4 * count++], port[0].fifo[i].m, port[0].fifo[i].n);
        from_fifo++;
    }
    for (int i = 0; count < 16 && i < SLOTS; i++)
        if (port[0].slot[i].used) {
            uint8_t c[3] = {(uint8_t)(0xB0 | port[0].slot[i].channel), port[0].slot[i].cc, port[0].slot[i].value};
            packet(&packets[4 * count++], c, 3);
            slots[slot_count++] = i;
        }
    if (!count) {
        if (usb_stalled && usb_midi_ready())
            usb_stalled = 0; // the host reads again (or is there at last), with nothing owed: from now on as before
        usb_ok_at = now; // nothing waiting is never a stall
        return;
    }
    if (!usb_midi_write(packets, count)) {
        if (!usb_stalled && now - usb_ok_at >= MIDIOUT_STALL_MS) { // the host stopped reading: keep only the offs owed
            usb_stalled = 1;
            port[0].tail = port[0].head;
            for (int i = 0; i < SLOTS; i++)
                port[0].slot[i].used = 0;
            port[0].clock = port[0].start_stop = port[0].ss_clocks = 0;
            for (int c = 0; c < 16; c++)
                for (int k = 0; k < 8; k++)
                    port[0].owed[c][k] |= port[0].on[c][k];
        }
        return;
    }
    usb_ok_at = now;
    usb_stalled = 0;
    for (int c = 0; c < 16; c++)
        for (int k = 0; k < 8; k++)
            if (owed_taken[c][k]) {
                port[0].owed[c][k] &= (uint16_t)~owed_taken[c][k];
                port[0].on[c][k] &= (uint16_t)~owed_taken[c][k];
            }
    port[0].clock = (uint8_t)(port[0].clock - clocks);
    if (start_stop)
        port[0].start_stop = port[0].ss_clocks = 0;
    else
        port[0].ss_clocks = (uint8_t)(port[0].ss_clocks > clocks ? port[0].ss_clocks - clocks : 0);
    for (int i = 0; i < from_fifo; i++) {
        sent(0, port[0].fifo[port[0].tail].m, port[0].fifo[port[0].tail].n);
        port[0].tail = (uint8_t)((port[0].tail + 1) % FIFO);
    }
    for (int i = 0; i < slot_count; i++)
        port[0].slot[slots[i]].used = 0;
    (void)owed;
}

void midiout_resume(uint32_t now_ms)
{
    usb_ok_at = now_ms; // the stall was ours (a tune): no host's
    forwarded_seen = forwarded_clocks; // and a clock forwarded meanwhile is stale: no burst of it
    forwarded_start_stops_seen = forwarded_start_stops;
}

int midiout_pending(int ports)
{
    for (int bit = 1; bit <= 2; bit <<= 1) {
        if (!(ports & bit))
            continue;
        int p = port_index(bit);
        if (port[p].tail != port[p].head || (p == 0 && (port[0].clock || port[0].start_stop)))
            return 1;
        for (int i = 0; i < SLOTS; i++)
            if (port[p].slot[i].used)
                return 1;
        for (int c = 0; c < 16; c++)
            for (int k = 0; k < 8; k++)
                if (port[p].owed[c][k])
                    return 1;
    }
    return 0;
}

void midiout_poll(uint32_t now_ms)
{
    if (!polled) { // the stall time counts from the first poll, not from 0 (power-up takes longer than the stall)
        polled = 1;
        usb_ok_at = now_ms;
    }
    din_poll(now_ms);
    poll_din();
    poll_usb(now_ms);
}

int midiout_usb_stalled(void) { return usb_stalled; }
