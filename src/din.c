// 5-pin MIDI in. The DIN socket's opto-isolator (U3, TLP2368, sheet 1) drives PA1, which is UART4's RX (AF8), so this
// is a plain 31250 baud receiver. The UART interrupt hands realtime bytes and Song Position Pointer straight to the
// clock handler with the cycle counter at arrival, as the USB driver does, and queues every other byte. The main loop
// turns the queue into USB MIDI event packets, running status included, so main.c parses both inputs the same way.
// Only channel messages come through: SysEx and the other system common messages are skipped (the editor and updates
// stay on USB).
// Out (0.10.0): PA0 is UART4's TX (AF8) into U4A, which ANDs it with U5A's copy of the IN socket; MIDI_SEL (PB4) lets
// that copy through (THRU, the stock default) or holds it high, so the socket gives PA0's bytes alone (OUT, MERGE). Two
// UART lines can't be ANDed byte for byte, so MERGE forwards the IN socket's bytes in software: the receive interrupt
// copies them into a ring and the transmit interrupt sends them, whole messages at a time between the synth's own.
// MIDI_SEL's level for THRU is read off the schematic (NOTES), not measured: MIDI_SEL_THRU is the one place to swap.
#include "din.h"
#include "clock.h"
#include "gpio.h"
#include "regs.h"

#define UART4_BASE 0x40004C00u
#define UART_SR    REG(UART4_BASE + 0x00)
#define UART_DR    REG(UART4_BASE + 0x04)
#define UART_BRR   REG(UART4_BASE + 0x08)
#define UART_CR1   REG(UART4_BASE + 0x0C)
#define SR_FE      (1u << 1)
#define SR_ORE     (1u << 3)
#define SR_RXNE    (1u << 5)
#define SR_TXE     (1u << 7)
#define CR1_RE     (1u << 2)
#define CR1_TE     (1u << 3)
#define CR1_RXNEIE (1u << 5)
#define CR1_TXEIE  (1u << 7)
#define MIDI_SEL     (1u << 4) // PB4
#define MIDI_SEL_THRU 0        // its level for THRU (U5A passes the IN socket); the other level holds U5A's output high
#define CR1_UE     (1u << 13)
#define UART4_IRQ  52
#define MIDI_BAUD  31250u

// Bytes from the socket: written by the interrupt, read by the main loop. 256 bytes are about 80 ms of a full
// stream, many passes' worth.
#define RX_BYTES 256
static uint8_t rx_ring[RX_BYTES];
static volatile uint32_t rx_head, rx_tail;
static void (*realtime)(uint8_t status, uint16_t data, uint32_t cycles);
static uint8_t song_bytes, song_lsb; // Song Position Pointer, parsed in the interrupt: 1 or 2 = that data byte next

// The parser, in the main loop: the channel status in force (0 = none) and the data bytes so far
static uint8_t running, data[2], count;

// Out: the synth's own messages, whole, from the main loop (din_send()); realtime owed, sent between any two bytes;
// in MERGE the IN socket's bytes. The transmit interrupt drains them; it alone pops, the main loop alone pushes ours.
#define TX_BYTES 64
#define THRU_BYTES 128
static uint8_t tx_ring[TX_BYTES], thru_ring[THRU_BYTES];
static volatile uint32_t tx_head, tx_tail, thru_head, thru_tail;
// Realtime owed: F8s as two counts, one the senders bump and one the interrupt bumps as it sends (neither writes the
// other's, so no update is lost), and the latest FA / FB / FC with counts of its own the same way, and the F8 count as
// it came (the clocks queued before it go first, the ones after it after)
static volatile uint8_t clocks_queued, clocks_sent, start_stop_byte, start_stop_queued, start_stop_sent, start_stop_clocks;
static volatile uint8_t socket = DIN_THRU, drop_input_clock;
static volatile uint32_t rx_bytes; // the socket's bytes but realtime (din_poll() sees a forwarded message stall)
// MERGE: the IN socket's realtime in a little queue of its own, in order, sent before anything but our own realtime
// (both ends are in the UART's one interrupt)
#define THRU_RT 8
static uint8_t thru_rt[THRU_RT], thru_rt_head, thru_rt_tail;
// The transmit side's place: the bytes left of the message going out (ours or a forwarded one: the other waits), a
// forwarded SysEx in progress, the forwarded stream's running status and the last status sent (a forwarded message in
// running status after one of ours gets its status again); the receive side's: a full thru ring dropping a message
// whole, until the next status that fits; a status owed before the socket goes back to THRU (din_prepare_thru())
static uint8_t own_left, thru_left, thru_sysex, thru_running, sent_status, thru_dropping;
static volatile uint8_t status_owed;

static int message_bytes(uint8_t status) // the data bytes after a status
{
    if (status < 0xF0)
        return (status & 0xE0) == 0xC0 ? 1 : 2;
    return status == 0xF2 ? 2 : status == 0xF1 || status == 0xF3 ? 1 : 0;
}

// The next byte for the socket, -1 for none, -2 for one dropped (ask again): realtime first (ours, in the order it came:
// a start or stop after the clocks queued before it, before the ones after; then the IN socket's), then the rest of
// the message going out, then (between messages) one of ours before a forwarded one, as a note shouldn't wait behind
// a long SysEx
static int tx_next(void)
{
    uint8_t queued = start_stop_queued;
    __asm volatile("" ::: "memory");
    if (queued != start_stop_sent && (clocks_sent == start_stop_clocks || clocks_sent == clocks_queued)) {
        start_stop_sent = queued;
        return start_stop_byte;
    }
    if (clocks_queued != clocks_sent) {
        clocks_sent++;
        return 0xF8;
    }
    if (thru_rt_tail != thru_rt_head) { // the IN socket's clock doesn't wait behind ours
        uint8_t b = thru_rt[thru_rt_tail];
        thru_rt_tail = (uint8_t)((thru_rt_tail + 1) % THRU_RT);
        return b;
    }
    if (status_owed && !own_left && !thru_left && !thru_sysex) { // before THRU: the IN socket's running status
        uint8_t b = status_owed;
        status_owed = 0;
        return sent_status = b;
    }
    // (a forwarded message part sent holds ours off only in MERGE: din_set_socket() and din_poll() end it otherwise)
    if (own_left || (!thru_left && !thru_sysex && tx_tail != tx_head)) {
        if (tx_tail == tx_head)
            return -1; // (din_send() publishes a message whole, so this can't happen: never the ring's stale bytes)
        uint8_t b = tx_ring[tx_tail];
        tx_tail = (tx_tail + 1) % TX_BYTES;
        if (b & 0x80) {
            own_left = (uint8_t)message_bytes(b);
            sent_status = b;
        } else if (own_left)
            own_left--;
        return b;
    }
    if (thru_tail == thru_head)
        return -1;
    uint8_t b = thru_ring[thru_tail]; // (never realtime: that has its own queue)
    if (!(b & 0x80) && !thru_left && !thru_sysex) { // a forwarded message in running status
        if (!thru_running) { // data with no status: dropped, as a receiver would
            thru_tail = (thru_tail + 1) % THRU_BYTES;
            return -2;
        }
        thru_left = (uint8_t)message_bytes(thru_running); // started: none of ours goes in between now
        if (sent_status != thru_running)
            return sent_status = thru_running; // its status again, then its data
    }
    thru_tail = (thru_tail + 1) % THRU_BYTES;
    if (b == 0xF0) {
        thru_sysex = 1;
        thru_running = 0;
    } else if (b == 0xF7) {
        thru_sysex = 0;
    } else if (b & 0x80) {
        thru_left = (uint8_t)message_bytes(b);
        thru_running = b < 0xF0 ? b : 0;
        thru_sysex = 0;
    } else if (thru_left)
        thru_left--;
    sent_status = b & 0x80 ? b : sent_status;
    return b;
}

static void tx_interrupt(void)
{
    int b;
    do
        b = tx_next();
    while (b == -2);
    if (b < 0)
        UART_CR1 &= ~CR1_TXEIE; // nothing left: the next din_send() or forwarded byte sets it again
    else
        UART_DR = (uint32_t)b;
}

void UART4_IRQHandler(void)
{
    uint32_t sr = UART_SR;
    if (sr & SR_TXE && UART_CR1 & CR1_TXEIE)
        tx_interrupt();
    if (!(sr & (SR_RXNE | SR_ORE)))
        return;
    uint8_t byte = (uint8_t)UART_DR; // reading SR then DR also clears an overrun or framing error
    if (sr & SR_FE)
        return; // a broken byte (a cable plugged in mid-stream): running status recovers at the next status
    if (byte < 0xF8)
        rx_bytes++;
    if (socket == DIN_MERGE && byte >= 0xF8) {
        uint8_t next = (uint8_t)((thru_rt_head + 1) % THRU_RT);
        if (next != thru_rt_tail && !(drop_input_clock && (byte == 0xF8 || byte == 0xFA || byte == 0xFB || byte == 0xFC))) {
            thru_rt[thru_rt_head] = byte;
            thru_rt_head = next;
            UART_CR1 |= CR1_TXEIE;
        }
    } else if (socket == DIN_MERGE) {
        uint32_t next = (thru_head + 1) % THRU_BYTES;
        if (thru_dropping && (byte < 0x80 || next == thru_tail))
            ; // the rest of a message that didn't fit: whole messages are lost, never parts paired with others
        else if (next == thru_tail)
            thru_dropping = 1; // full: lost to the forwarded stream, as with any soft thru
        else {
            thru_dropping = 0;
            thru_ring[thru_head] = byte;
            thru_head = next;
            UART_CR1 |= CR1_TXEIE;
        }
    }
    if (byte >= 0xF8) {
        if (realtime)
            realtime(byte, 0, DWT_CYCCNT);
        return;
    }
    if (byte & 0x80) {
        song_bytes = byte == 0xF2;
    } else if (song_bytes == 1) {
        song_lsb = byte;
        song_bytes = 2;
    } else if (song_bytes == 2) {
        song_bytes = 0;
        if (realtime)
            realtime(0xF2, (uint16_t)(song_lsb | byte << 7), DWT_CYCCNT);
    }
    uint32_t next = (rx_head + 1) % RX_BYTES;
    if (next != rx_tail) {
        rx_ring[rx_head] = byte;
        __asm volatile("" ::: "memory"); // the byte is in the ring before the main loop can see it
        rx_head = next;
    }
}

// PA0 high from the start (reset_body() and main(), beside the S&H enables): until the UART drives it, a floating PA0
// is half of U4A's AND, so THRU would follow noise through the boot. Registers only: it runs before .data.
void din_safe(void)
{
    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOA;
    (void)RCC_AHB1ENR; // (the first write after a clock enable can be lost)
    gpio_write(GPIOA_BASE, 1u << 0, 1);
    gpio_output(GPIOA_BASE, 1u << 0);
}

void din_init(void (*handler)(uint8_t status, uint16_t data, uint32_t cycles))
{
    realtime = handler;
    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOA | RCC_AHB1ENR_GPIOB;
    RCC_APB1ENR |= RCC_APB1ENR_UART4;
    din_set_socket(socket);
    uint32_t apb1 = system_hz > HSI_HZ ? system_hz / 4 : system_hz; // clock_init(): APB1 = AHB / 4 on the PLL
    UART_BRR = (apb1 + MIDI_BAUD / 2) / MIDI_BAUD; // 16x oversampling: BRR = f / baud (42 MHz: 1344)
    UART_CR1 = CR1_UE | CR1_RE | CR1_TE | CR1_RXNEIE; // TX idles high before PA0 is handed over: no glitch
    gpio_alternate(GPIOA_BASE, 1u << 1, 8); // R10 pulls the line up when the socket is idle
    gpio_alternate(GPIOA_BASE, 1u << 0, 8); // TX: from din_safe()'s high to the UART's idle high
    NVIC_IPR(UART4_IRQ) = 0x80; // as USB: both feed the MIDI clock, so neither may interrupt the other
    NVIC_ISER(UART4_IRQ / 32) = 1u << (UART4_IRQ % 32);
}

static int data_bytes(uint8_t status) { return (status & 0xE0) == 0xC0 ? 1 : 2; } // program change, pressure: 1

int din_midi_read(uint8_t packet[4])
{
    while (rx_tail != rx_head) {
        uint8_t byte = rx_ring[rx_tail];
        __asm volatile("" ::: "memory"); // read before the interrupt may overwrite it
        rx_tail = (rx_tail + 1) % RX_BYTES;
        if (byte & 0x80) { // a channel status starts running status; SysEx and system common end it
            running = byte < 0xF0 ? byte : 0;
            count = 0;
            continue;
        }
        if (!running)
            continue; // SysEx or system common data, or data before any status
        data[count++] = byte;
        if (count < data_bytes(running))
            continue;
        count = 0;
        packet[0] = running >> 4; // cable 0; a channel message's code index number is its status nibble
        packet[1] = running;
        packet[2] = data[0];
        packet[3] = data_bytes(running) == 2 ? data[1] : 0;
        return 1;
    }
    return 0;
}

void din_midi_flush(void)
{
    rx_tail = rx_head;
    running = 0;
    count = 0;
}

// The forwarded stream starts afresh: nothing of it waiting or part sent (the interrupt held off meanwhile)
static void thru_reset(void)
{
    NVIC_ICER(UART4_IRQ / 32) = 1u << (UART4_IRQ % 32);
#ifdef __arm__
    __asm volatile("dsb\n isb" ::: "memory"); // the disable has taken effect (the simulator builds this for the host)
#else
    __asm volatile("" ::: "memory");
#endif
    thru_tail = thru_head;
    thru_rt_tail = thru_rt_head;
    thru_left = thru_sysex = thru_running = thru_dropping = 0;
    if (socket == DIN_THRU) { // nothing of ours goes out here: what's left of it would be ANDed into the IN stream
        tx_tail = tx_head;
        own_left = 0;
        clocks_sent = clocks_queued;
        start_stop_sent = start_stop_queued;
        status_owed = 0;
    }
    __asm volatile("" ::: "memory");
    NVIC_ISER(UART4_IRQ / 32) = 1u << (UART4_IRQ % 32);
}

void din_set_socket(int mode)
{
    uint8_t was = socket;
    socket = (uint8_t)(mode == DIN_OUT || mode == DIN_MERGE ? mode : DIN_THRU);
    if (socket != was) // (before MIDI_SEL: to THRU, nothing of ours is left to go out under the IN stream)
        thru_reset(); // a forwarded SysEx or message part would otherwise hold the synth's own off for good
    gpio_write(GPIOB_BASE, MIDI_SEL, socket == DIN_THRU ? MIDI_SEL_THRU : !MIDI_SEL_THRU);
    gpio_output(GPIOB_BASE, MIDI_SEL);
}

// A forwarded message the IN socket left unfinished (a cable pulled in a SysEx dump, a device switched off) holds ours
// off only so long: after DIN_THRU_STALL_MS with no byte in and nothing of it waiting it's given up (the receiver
// takes the synth's next status as its end)
#define DIN_THRU_STALL_MS 50
void din_poll(uint32_t now_ms)
{
    static uint32_t seen, since;
    uint32_t bytes = rx_bytes; // (realtime not counted: an IN clock goes on through a cut-off dump)
    if (bytes != seen || !(thru_left || thru_sysex) || thru_tail != thru_head || tx_tail == tx_head) { // a slow SysEx
        // with nothing of ours waiting behind it goes on as long as it takes
        seen = bytes;
        since = now_ms;
    } else if (now_ms - since >= DIN_THRU_STALL_MS) {
        thru_reset();
        UART_CR1 |= CR1_TXEIE; // ours, if any wait
        since = now_ms;
    }
}

int din_socket(void) { return socket; }

int din_tx_room(void) { return (int)((tx_tail + TX_BYTES - tx_head - 1) % TX_BYTES); }

int din_send(const uint8_t *bytes, int n)
{
    if (socket == DIN_THRU || n > din_tx_room())
        return 0;
    uint32_t head = tx_head;
    for (int i = 0; i < n; i++) {
        tx_ring[head] = bytes[i];
        head = (head + 1) % TX_BYTES;
    }
    __asm volatile("" ::: "memory"); // the whole message is in the ring before the interrupt can see any of it
    tx_head = head;
    UART_CR1 |= CR1_TXEIE;
    return 1;
}

// From the main loop (the synth's clock) or the USB interrupt (a followed clock forwarded): one at a time, as the
// clock source decides which
void din_realtime(uint8_t byte)
{
    if (socket == DIN_THRU)
        return;
    if (byte == 0xF8) {
        if ((uint8_t)(clocks_queued - clocks_sent) < 250)
            clocks_queued++;
    } else {
        start_stop_byte = byte;
        start_stop_clocks = clocks_queued;
        __asm volatile("" ::: "memory");
        start_stop_queued++;
    }
    UART_CR1 |= CR1_TXEIE;
}

int din_tx_idle(void)
{
    return tx_tail == tx_head && clocks_queued == clocks_sent && start_stop_queued == start_stop_sent && !status_owed;
}

// Before the socket goes back to THRU: the IN socket's raw bytes follow, so a receiver left on one of the synth's statuses
// would read a running-status keyboard's data under it. The IN stream's status (the parser's) goes out first.
void din_prepare_thru(void)
{
    if (socket != DIN_THRU && running && running != sent_status) {
        status_owed = running;
        UART_CR1 |= CR1_TXEIE;
    }
}

void din_drop_input_clock(int on) { drop_input_clock = (uint8_t)(on != 0); }
