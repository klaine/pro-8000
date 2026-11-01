// The real src/usb.c against a model of the OTG core's registers, for tools/test_panel.py: `usbharness <scenario>` exits
// 0 and prints "ok" and its counts, or names the failed check. The firmware's simulator (hw.c) replaces usb.c, so this
// is what covers the driver's flow control: its sequence of register writes against what the model below assumes. It
// says nothing about the core itself, and only the synth can show the NAKs and the races are as assumed. The interrupt is
// a plain call, made between the driver's functions and at the one point a test can reach inside them, as the driver goes
// to mask the interrupt (preempt_hook).
//
// What the model does, from RM0390 chapter 30: the MIDI OUT endpoint takes one packet only while it is active (USBAEP),
// enabled (EPENA), not NAKing (CNAK) and given a packet to take (DOEPTSIZ), otherwise it NAKs and the host keeps the
// packet. Taking one clears EPENA, sets the NAK again and uses up DOEPTSIZ. EPENA can't be cleared by writing a 0, and a
// bus reset clears only USBAEP, so an endpoint armed before a reset stays enabled: taken as the worst case, from memory of
// the manual. The packet's DATA0/DATA1 must match the toggle the endpoint expects, or the core ACKs it and throws it away.
// A packet shows in the receive FIFO as a data entry and then a "transfer complete" entry, and popping that one raises
// XFRC, which the driver sees through DAINT and OEPINT. sim_reg() can't tell a read from a write, so the writes that
// matter are noticed at the next access.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "usb.h"

extern void OTG_FS_IRQHandler(void);

#define OTG      0x50000000u
#define GRSTCTL  (OTG + 0x010)
#define GINTSTS  (OTG + 0x014)
#define GRXSTSP  (OTG + 0x020)
#define DAINT    (OTG + 0x818)
#define DIEPCTL1 (OTG + 0x920)
#define DOEPCTL1 (OTG + 0xB20)
#define DOEPINT0 (OTG + 0xB08)
#define DOEPINT1 (OTG + 0xB28)
#define DOEPTSIZ1 (OTG + 0xB30)
#define FIFO0    (OTG + 0x1000)
#define DWT      0xE0001004u
#define NVIC_SET 0xE000E108u // the USB interrupt (67) is in the third word
#define NVIC_CLR 0xE000E188u

#define INT_USBRST (1u << 12)
#define INT_RXFLVL (1u << 4)
#define INT_OEPINT (1u << 19)
#define IDLE       (1u << 31)
#define RXFLUSH    (1u << 4)
#define EPENA      (1u << 31)
#define USBAEP     (1u << 15)
#define CNAK       (1u << 26)
#define SNAK       (1u << 27)
#define SD0PID     (1u << 28)
#define TSIZ_ONE_PACKET ((1u << 19) | 64u)
#define XFRC       1u
#define STUP       8u
#define OTG_IRQ    (1u << 3)
#define OUT_DATA   2u
#define OUT_DONE   3u
#define SETUP_DONE 4u
#define SETUP_DATA 6u

// --- the model ---

#define REG_SLOTS 1024
static struct { uint32_t address, value; } reg_table[REG_SLOTS];

static uint32_t *slot_of(uint32_t address)
{
    for (uint32_t i = (address >> 2) % REG_SLOTS;; i = (i + 1) % REG_SLOTS) {
        if (reg_table[i].address == address)
            return &reg_table[i].value;
        if (!reg_table[i].address) {
            reg_table[i].address = address;
            return &reg_table[i].value;
        }
    }
}

// The receive FIFO: entries and their data words, in order
static uint32_t rx_fifo[256];
static int fifo_head, fifo_tail, fifo_data_left;
static uint32_t gintsts, daint, doepint[2], cycles;
static int irq_masked, in_interrupt, configuring;
static int naks, packets, lost, arms, arms_while_armed, arms_unmasked, arms_dead;
static int out_enabled, out_naking = 1, out_toggle, host_toggle, out_toggle_resets, in_toggle_resets;
static void (*preempt_hook)(void);

static void fifo_put(uint32_t word)
{
    rx_fifo[fifo_head] = word;
    fifo_head = (fifo_head + 1) % 256;
}

static void raise_doepint(int ep, uint32_t flag)
{
    doepint[ep] |= flag;
    daint |= 1u << (16 + ep);
    gintsts |= INT_OEPINT;
    *slot_of(ep ? DOEPINT1 : DOEPINT0) = doepint[ep];
}

// What the last access wrote
static void notice_writes(void)
{
    uint32_t *mask = slot_of(NVIC_CLR), *unmask = slot_of(NVIC_SET), *reset = slot_of(GRSTCTL);
    if (*mask & OTG_IRQ)
        irq_masked = 1;
    if (*unmask & OTG_IRQ)
        irq_masked = 0;
    *mask = *unmask = 0;
    if (*reset & RXFLUSH)
        fifo_head = fifo_tail = fifo_data_left = 0;
    uint32_t *out = slot_of(DOEPCTL1), *in = slot_of(DIEPCTL1);
    if (*out & EPENA)
        out_enabled = 1;
    if (*out & CNAK)
        out_naking = 0;
    if (*out & SNAK)
        out_naking = 1;
    if (*out & SD0PID) {
        out_toggle = 0;
        out_toggle_resets++;
    }
    *out = (*out & ~(EPENA | CNAK | SNAK | SD0PID)) | (out_enabled ? EPENA : 0);
    if (*in & SD0PID) {
        in_toggle_resets++;
        *in &= ~SD0PID;
    }
    for (int ep = 0; ep < 2; ep++) { // writing ones clears; writing back what was read is cleared by interrupt()
        uint32_t *flags = slot_of(ep ? DOEPINT1 : DOEPINT0);
        if (*flags != doepint[ep]) {
            doepint[ep] &= ~*flags;
            *flags = doepint[ep];
        }
    }
}

volatile uint32_t *sim_reg(uint32_t address)
{
    notice_writes();
    if (address == NVIC_CLR && preempt_hook && !in_interrupt && !irq_masked)
        preempt_hook();
    uint32_t *slot = slot_of(address);
    switch (address) {
    case GINTSTS:
        *slot = gintsts | (fifo_tail != fifo_head ? INT_RXFLVL : 0);
        break;
    case DAINT:
        *slot = daint;
        break;
    case GRSTCTL: // every reset and flush is done at once
        *slot = IDLE;
        break;
    case GRXSTSP:
        *slot = rx_fifo[fifo_tail];
        fifo_tail = (fifo_tail + 1) % 256;
        fifo_data_left = (*slot >> 4 & 0x7FF) / 4;
        if ((*slot >> 17 & 0xF) == OUT_DONE)
            raise_doepint(1, XFRC);
        if ((*slot >> 17 & 0xF) == SETUP_DONE)
            raise_doepint(0, STUP);
        break;
    case FIFO0: // reads pop while the entry has data; the driver's writes to EP0's TX FIFO land here harmlessly
        if (fifo_data_left) {
            *slot = rx_fifo[fifo_tail];
            fifo_tail = (fifo_tail + 1) % 256;
            fifo_data_left--;
        }
        break;
    case DOEPTSIZ1: // only midi_out_arm() touches it
        arms++;
        if ((*slot_of(DOEPCTL1) & EPENA) && !configuring) // (SET_CONFIGURATION may find it enabled: nothing has used it up)
            arms_while_armed++;
        if (!(*slot_of(DOEPCTL1) & USBAEP))
            arms_dead++;
        if (!in_interrupt && !irq_masked)
            arms_unmasked++;
        break;
    case DWT:
        *slot = ++cycles;
        break;
    default:
        break;
    }
    return slot;
}

void delay_us(uint32_t us) { (void)us; }

// One interrupt, and again while anything is pending: the driver takes a snapshot of GINTSTS, so the XFRC that popping
// a packet raises is handled on the next entry, as on the chip.
static void interrupt(void)
{
    in_interrupt = 1;
    do {
        uint32_t handled = gintsts;
        OTG_FS_IRQHandler();
        notice_writes();
        gintsts &= ~(handled & INT_USBRST);
        if (handled & INT_OEPINT) { // it wrote back what it read
            gintsts &= ~INT_OEPINT;
            daint = 0;
            doepint[0] = doepint[1] = 0;
            *slot_of(DOEPINT0) = *slot_of(DOEPINT1) = 0;
        }
    } while (gintsts || fifo_tail != fifo_head);
    in_interrupt = 0;
}

static int irq_open(void)
{
    notice_writes(); // the driver's last write
    return !irq_masked;
}

static int armed(void)
{
    notice_writes(); // the driver's last write
    return (*slot_of(DOEPCTL1) & (EPENA | USBAEP)) == (EPENA | USBAEP) && !out_naking && *slot_of(DOEPTSIZ1) == TSIZ_ONE_PACKET;
}

// The host sends one packet: 0 if the endpoint NAKs it.
static int host_send(const uint32_t *words, int count)
{
    if (!armed()) {
        naks++;
        return 0;
    }
    if (host_toggle != out_toggle) { // taken for a retry of the last one
        host_toggle = !host_toggle;
        lost++;
        return 1;
    }
    host_toggle = !host_toggle;
    out_toggle = !out_toggle;
    packets++;
    out_enabled = 0;
    out_naking = 1;
    *slot_of(DOEPCTL1) &= ~EPENA;
    *slot_of(DOEPTSIZ1) = 64u - (uint32_t)count * 4;
    fifo_put(1u | (uint32_t)count * 4 << 4 | OUT_DATA << 17);
    for (int i = 0; i < count; i++)
        fifo_put(words[i]);
    fifo_put(1u | OUT_DONE << 17);
    interrupt();
    return 1;
}

static void bus_reset(void)
{
    *slot_of(DOEPCTL1) &= ~USBAEP;
    gintsts |= INT_USBRST;
    interrupt();
}

static void control_request(uint32_t type, uint32_t request, uint32_t value, uint32_t index)
{
    fifo_put(0u | 8u << 4 | SETUP_DATA << 17);
    fifo_put(type | request << 8 | value << 16);
    fifo_put(index);
    fifo_put(0u | SETUP_DONE << 17);
    interrupt();
}

static void set_configuration(void)
{
    host_toggle = 0;
    configuring = 1;
    control_request(0, 9, 1, 0);
    configuring = 0;
}

#define SET_INTERFACE 11
#define CLEAR_FEATURE 1
#define TO_INTERFACE  1
#define TO_ENDPOINT   2
#define ENDPOINT_HALT 0

// A pipe reset from the host: its own toggle goes back to DATA0, whatever the device does about it.
static void clear_halt(uint32_t endpoint)
{
    if (endpoint == 1)
        host_toggle = 0;
    control_request(TO_ENDPOINT, CLEAR_FEATURE, ENDPOINT_HALT, endpoint);
}

static void set_interface(uint32_t interface)
{
    if (interface == 1)
        host_toggle = 0;
    control_request(TO_INTERFACE, SET_INTERFACE, 0, interface);
}

// --- the host's side and the main loop's ---

#define CHECK(condition) \
    do { \
        if (!(condition)) { \
            printf("failed, line %d: %s\n", __LINE__, #condition); \
            exit(1); \
        } \
    } while (0)

static uint32_t next_id, expected_id;
static uint8_t realtime_status[64];
static uint16_t realtime_data[64];
static uint32_t realtime_cycles[64];
static int realtime_count;

static void on_realtime(uint8_t status, uint16_t data, uint32_t at)
{
    CHECK(realtime_count < 64);
    realtime_status[realtime_count] = status;
    realtime_data[realtime_count] = data;
    realtime_cycles[realtime_count++] = at;
}

static uint32_t event(uint32_t id) { return 0x0Bu | 0xB0u << 8 | (id & 0x7F) << 16 | (id >> 7 & 0x7F) << 24; }

// The next `count` events in one packet: 0 if the endpoint NAKs it, and the ids stay as they were.
static int send_events(int count)
{
    uint32_t words[16];
    for (int i = 0; i < count; i++)
        words[i] = event(next_id + (uint32_t)i);
    if (!host_send(words, count))
        return 0;
    next_id += (uint32_t)count;
    return 1;
}

// One read, checked to be the next event in order. 0 when the ring is empty.
static uint32_t rx_waiting(void) { return next_id - expected_id; }

static int read_event(void)
{
    uint8_t p[4];
    int got = usb_midi_read(p);
    CHECK(irq_open());
    if (!got)
        return 0;
    CHECK(p[0] == 0x0B && p[1] == 0xB0);
    CHECK((uint32_t)(p[2] | p[3] << 7) == (expected_id & 0x3FFF));
    expected_id++;
    return 1;
}

static void read_events(int count)
{
    for (int i = 0; i < count; i++)
        CHECK(read_event());
}

static int configured; // what the host has done, for the random walk

static void start(void)
{
    usb_midi_realtime(on_realtime);
    usb_init();
    CHECK(irq_open());
    bus_reset();
    CHECK(!armed());
    set_configuration();
    CHECK(armed());
    configured = 1;
    arms = 0;
}

// 16 + 16 + 16 events leave room for 15: it parks with 48 waiting.
static void park(void)
{
    for (int i = 0; i < 3; i++) {
        CHECK(armed());
        CHECK(send_events(16));
    }
    CHECK(!armed());
}

static void check_clean(void)
{
    CHECK(arms_while_armed == 0);
    CHECK(arms_unmasked == 0);
    CHECK(arms_dead == 0);
    CHECK(lost == 0);
    CHECK(irq_open());
}

// --- scenarios ---

static void walk_hook_reset(void)
{
    preempt_hook = 0;
    bus_reset();
    configured = 0;
}

static void walk_hook_reset_and_configure(void)
{
    preempt_hook = 0;
    bus_reset();
    set_configuration();
    configured = 1;
}

static void burst(void)
{
    start();
    int sent = 0;
    while (sent < 500) {
        int count = 500 - sent < 16 ? 500 - sent : 16;
        if (send_events(count))
            sent += count;
        else
            for (int i = 0; i < 5; i++) // the main loop gets round to a few between the host's tries
                CHECK(read_event());
    }
    while (read_event())
        ;
    CHECK(expected_id == 500);
    CHECK(naks > 0);
    CHECK(armed());
    check_clean();
}

static void parks_and_unparks(void)
{
    start();
    CHECK(send_events(16));
    CHECK(send_events(16));
    CHECK(send_events(15)); // 47 waiting, room for 16: this one still arms
    CHECK(armed());
    CHECK(send_events(16)); // the fullest it can get: every slot used, nothing dropped
    CHECK(!armed());
    int arms_before = arms;
    CHECK(!send_events(1));
    CHECK(naks == 1);
    read_events(14);
    CHECK(!armed()); // 49 waiting
    read_events(1);
    CHECK(!armed()); // 48 waiting: room for 15
    CHECK(arms == arms_before);
    read_events(1);
    CHECK(armed()); // 47 waiting: room for 16
    CHECK(arms == arms_before + 1);
    read_events(40);
    CHECK(arms == arms_before + 1); // only the one
    CHECK(send_events(16));
    while (read_event())
        ;
    CHECK(expected_id == next_id);
    CHECK(armed());
    check_clean();
}

static void flush_unparks(void)
{
    start();
    usb_midi_flush();
    CHECK(irq_open());
    CHECK(armed());
    CHECK(arms == 0); // armed already: not again
    park();
    usb_midi_flush();
    CHECK(irq_open());
    CHECK(armed());
    CHECK(!read_event()); // nothing left
    expected_id = next_id;
    CHECK(send_events(16));
    read_events(16);
    check_clean();
}

static void reset_clears_parked(void)
{
    start();
    park();
    int arms_before = arms;
    bus_reset();
    CHECK(!armed());
    read_events(20); // room again: it must not arm an endpoint that's gone
    CHECK(!armed());
    CHECK(arms == arms_before);
    set_configuration();
    CHECK(armed());
    CHECK(arms == arms_before + 1);
    CHECK(send_events(16));
    CHECK(send_events(4));
    read_events(48);
    CHECK(!read_event());
    CHECK(armed());

    // an XFRC that the reset overtakes doesn't arm anything
    arms_before = arms;
    raise_doepint(1, XFRC);
    bus_reset();
    CHECK(!armed());
    CHECK(arms == arms_before);

    // nor one still pending at SET_CONFIGURATION: one arm for the configuration, none for the old packet
    raise_doepint(1, XFRC);
    set_configuration();
    CHECK(armed());
    CHECK(arms == arms_before + 1);

    // the ring may be nearly full by the time the host configures again: then a packet wouldn't fit, so it stays parked
    park();
    bus_reset();
    set_configuration();
    CHECK(!armed());
    read_events(1);
    CHECK(armed());
    while (read_event())
        ;
    CHECK(expected_id == next_id);
    check_clean();
}

static void discard_mode(void)
{
    start();
    park();
    usb_midi_discard(1); // un-parks
    CHECK(irq_open());
    CHECK(armed());
    int arms_before = arms;
    for (int i = 0; i < 20; i++) { // far over what fits: the host isn't held up
        CHECK(send_events(16));
        CHECK(armed());
    }
    CHECK(naks == 0);
    CHECK(arms == arms_before + 20);
    read_events(63); // the first ones, and the rest dropped
    CHECK(!read_event());
    CHECK(armed());
    CHECK(arms == arms_before + 20); // reading doesn't arm it again
    usb_midi_discard(1); // already on and armed: nothing happens
    CHECK(arms == arms_before + 20);

    // over a stall: then a flush and back to waiting
    for (int i = 0; i < 8; i++)
        CHECK(send_events(16));
    usb_midi_flush();
    usb_midi_discard(0);
    CHECK(irq_open());
    CHECK(armed());
    expected_id = next_id;
    park();
    CHECK(!send_events(16));
    CHECK(naks == 1);
    while (read_event())
        ;
    CHECK(expected_id == next_id);
    CHECK(armed());
    check_clean();
}

static void realtime_bytes(void)
{
    start();
    // between events, skipping the ring, stamped with the cycle counter
    uint32_t words[4] = {event(0), 0x0Fu | 0xF8u << 8, event(1), 0x03u | 0xF2u << 8 | 0x12u << 16 | 0x34u << 24};
    CHECK(host_send(words, 4));
    next_id = 2;
    CHECK(realtime_count == 2);
    CHECK(realtime_status[0] == 0xF8 && realtime_data[0] == 0 && realtime_cycles[0] != 0);
    CHECK(realtime_status[1] == 0xF2 && realtime_data[1] == (0x12 | 0x34 << 7));
    read_events(2);
    CHECK(!read_event());

    // parked, the byte waits with the rest and arrives as soon as a read makes room
    uint32_t tick = 0x0Fu | 0xF8u << 8;
    park();
    CHECK(!host_send(&tick, 1));
    CHECK(realtime_count == 2);
    read_events(1);
    CHECK(armed());
    CHECK(host_send(&tick, 1));
    CHECK(realtime_count == 3);

    // while discarding, a full ring doesn't touch it
    usb_midi_discard(1);
    for (int i = 0; i < 8; i++)
        CHECK(send_events(16));
    CHECK(host_send(&tick, 1));
    CHECK(realtime_count == 4);
    usb_midi_flush();
    usb_midi_discard(0);
    check_clean();
}

// A host resetting a pipe, an odd number of packets in, when the endpoint expects DATA1 and the host starts from DATA0
static void toggles(void)
{
    start();
    CHECK(send_events(1));
    int in = in_toggle_resets, out = out_toggle_resets;
    clear_halt(0x01);
    CHECK(send_events(1)); // arrives
    CHECK(in_toggle_resets == in);
    CHECK(out_toggle_resets == out + 1);
    read_events(2);

    CHECK(send_events(1));
    clear_halt(0x81); // the IN pipe's: the OUT toggle runs on
    CHECK(in_toggle_resets == in + 1);
    CHECK(out_toggle_resets == out + 1);
    CHECK(send_events(1));
    read_events(2);

    CHECK(send_events(1));
    set_interface(1);
    CHECK(in_toggle_resets == in + 2);
    CHECK(send_events(1));
    read_events(2);

    // none of these reset a pipe, so the host's toggle and the endpoint's both run on
    CHECK(send_events(1));
    out = out_toggle_resets;
    set_interface(0); // the audio control interface has no endpoints
    clear_halt(0x02); // no such endpoint
    control_request(0, CLEAR_FEATURE, 1, 0); // DEVICE_REMOTE_WAKEUP
    control_request(TO_ENDPOINT, CLEAR_FEATURE, 1, 0x01); // no such feature
    CHECK(out_toggle_resets == out);
    CHECK(in_toggle_resets == in + 2);
    CHECK(send_events(1));
    read_events(2);

    // parked it stays parked, and the first packet after it opens arrives
    park();
    set_interface(1);
    CHECK(!armed());
    read_events(1);
    CHECK(armed());
    CHECK(send_events(1));
    while (read_event())
        ;
    CHECK(expected_id == next_id);

    // before SET_CONFIGURATION the endpoints aren't ours to touch
    bus_reset();
    in = in_toggle_resets;
    out = out_toggle_resets;
    clear_halt(0x01);
    set_interface(1);
    CHECK(in_toggle_resets == in);
    CHECK(out_toggle_resets == out);
    set_configuration();
    CHECK(send_events(1));
    read_events(1);
    check_clean();
}

// The interrupt comes in as the read that makes room goes to mask it: between its look at `parked` and the mask.
static void hook_reset(void)
{
    preempt_hook = 0;
    bus_reset();
}

static void hook_reset_and_configure(void)
{
    preempt_hook = 0;
    bus_reset();
    set_configuration();
}

static void preempted(void)
{
    start();
    park();
    read_events(1); // 47 waiting, and open again
    CHECK(armed());
    CHECK(send_events(16));
    CHECK(!armed());
    read_events(15); // 48 waiting
    CHECK(!armed());
    int arms_before = arms;
    preempt_hook = hook_reset;
    read_events(1);
    CHECK(preempt_hook == 0);
    CHECK(!armed()); // a dead endpoint isn't armed
    CHECK(arms == arms_before);

    set_configuration();
    CHECK(armed());
    while (armed())
        CHECK(send_events(16));
    while (rx_waiting() > 48)
        read_events(1);
    arms_before = arms;
    preempt_hook = hook_reset_and_configure;
    read_events(1);
    CHECK(preempt_hook == 0);
    CHECK(armed());
    CHECK(arms == arms_before + 1); // SET_CONFIGURATION's: not a second one on top
    check_clean();

    // usb_midi_discard() looks only after the mask, so a reset before that needs nothing more
    while (armed())
        CHECK(send_events(16));
    arms_before = arms;
    preempt_hook = hook_reset;
    usb_midi_discard(1);
    CHECK(preempt_hook == 0);
    CHECK(!armed());
    CHECK(arms == arms_before);
    usb_midi_discard(0);
    check_clean();
}

// Random sends, reads and flushes, with resets, pipe resets and discard spells: nothing lost or out of order outside
// discard, and an empty ring always has the endpoint open.
static void random_walk(void)
{
    start();
    uint32_t seed = 12345;
    for (int step = 0; step < 200000; step++) {
        seed = seed * 1664525u + 1013904223u;
        uint32_t r = seed >> 8;
        switch (r % 100) {
        default: // send
            if (configured)
                send_events(1 + (int)(r >> 8) % 16);
            break;
        case 0: case 1: case 2: case 3: case 4: case 5: case 6: case 7: case 8: case 9:
        case 10: case 11: case 12: case 13: case 14: case 15: case 16: case 17: case 18: case 19:
        case 20: case 21: case 22: case 23: case 24: case 25: case 26: case 27: case 28: case 29: { // the main loop reads
            int reads = (int)(r >> 8) % 40;
            for (int i = 0; i < reads; i++)
                if (!read_event()) {
                    CHECK(!configured || armed());
                    break;
                }
            break;
        }
        case 30:
            if ((r >> 8) % 8 == 0) {
                usb_midi_flush();
                expected_id = next_id;
                CHECK(!configured || armed());
            }
            break;
        case 31:
            if ((r >> 8) % 16 == 0) {
                bus_reset();
                configured = 0;
            }
            break;
        case 32:
            if (!configured || (r >> 8) % 16 == 0) { // a second SET_CONFIGURATION finds the endpoint live
                set_configuration();
                configured = 1;
            }
            break;
        case 34:
            if (configured)
                (r >> 8) % 2 ? clear_halt(0x01) : set_interface(1);
            break;
        case 35:
            preempt_hook = !configured ? 0 : (r >> 8) % 2 ? walk_hook_reset : walk_hook_reset_and_configure; // the next time it goes to mask
            break;
        case 33:
            if (configured && (r >> 8) % 16 == 0) { // a spell of discarding, flushed at its end
                preempt_hook = 0;
                usb_midi_discard(1);
                CHECK(armed());
                for (int i = 0; i < 12; i++)
                    CHECK(send_events(16));
                usb_midi_flush();
                usb_midi_discard(0);
                expected_id = next_id;
                CHECK(armed());
            }
            break;
        }
        CHECK(irq_open());
    }
    preempt_hook = 0;
    while (read_event())
        ;
    CHECK(expected_id == next_id);
    if (!configured)
        set_configuration();
    CHECK(armed());
    check_clean();
}

int main(int argc, char **argv)
{
    static const struct { const char *name; void (*run)(void); } scenarios[] = {
        {"burst", burst},
        {"park", parks_and_unparks},
        {"flush", flush_unparks},
        {"reset", reset_clears_parked},
        {"discard", discard_mode},
        {"realtime", realtime_bytes},
        {"toggles", toggles},
        {"preempted", preempted},
        {"random", random_walk},
    };
    for (size_t i = 0; argc == 2 && i < sizeof scenarios / sizeof scenarios[0]; i++)
        if (!strcmp(argv[1], scenarios[i].name)) {
            scenarios[i].run();
            printf("ok naks=%d packets=%d arms=%d events=%u\n", naks, packets, arms, next_id);
            return 0;
        }
    fprintf(stderr, "usage: usbharness burst|park|flush|reset|discard|realtime|toggles|preempted|random\n");
    return 2;
}
