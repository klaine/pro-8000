// 5-pin MIDI in. The DIN socket's opto-isolator (U3, TLP2368, sheet 1) drives PA1, which is UART4's RX (AF8), so this
// is a plain 31250 baud receiver. The UART interrupt hands realtime bytes straight to the clock handler with the cycle
// counter at arrival, as the USB driver does, and queues every other byte. The main loop turns the queue into USB
// MIDI event packets, running status included, so main.c parses both inputs the same way. Only channel messages
// come through: SysEx and the other system common messages are skipped (the editor and updates stay on USB).
// PA0 (UART4 TX) and MIDI_SEL feed the MIDI THRU socket through U4A / U5A; they're left alone.
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
#define CR1_RE     (1u << 2)
#define CR1_RXNEIE (1u << 5)
#define CR1_UE     (1u << 13)
#define UART4_IRQ  52
#define MIDI_BAUD  31250u

// Bytes from the socket: written by the interrupt, read by the main loop. 256 bytes are about 80 ms of a full
// stream, many passes' worth.
#define RX_BYTES 256
static uint8_t rx_ring[RX_BYTES];
static volatile uint32_t rx_head, rx_tail;
static void (*realtime)(uint8_t status, uint32_t cycles);

// The parser, in the main loop: the channel status in force (0 = none) and the data bytes so far
static uint8_t running, data[2], count;

void UART4_IRQHandler(void)
{
    uint32_t sr = UART_SR;
    if (!(sr & (SR_RXNE | SR_ORE)))
        return;
    uint8_t byte = (uint8_t)UART_DR; // reading SR then DR also clears an overrun or framing error
    if (sr & SR_FE)
        return; // a broken byte (a cable plugged in mid-stream): running status recovers at the next status
    if (byte >= 0xF8) {
        if (realtime)
            realtime(byte, DWT_CYCCNT);
        return;
    }
    uint32_t next = (rx_head + 1) % RX_BYTES;
    if (next != rx_tail) {
        rx_ring[rx_head] = byte;
        rx_head = next;
    }
}

void din_init(void (*handler)(uint8_t status, uint32_t cycles))
{
    realtime = handler;
    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOA;
    RCC_APB1ENR |= RCC_APB1ENR_UART4;
    gpio_alternate(GPIOA_BASE, 1u << 1, 8); // R10 pulls the line up when the socket is idle
    uint32_t apb1 = system_hz > HSI_HZ ? system_hz / 4 : system_hz; // clock_init(): APB1 = AHB / 4 on the PLL
    UART_BRR = (apb1 + MIDI_BAUD / 2) / MIDI_BAUD; // 16× oversampling: BRR = f / baud (42 MHz: 1344)
    UART_CR1 = CR1_UE | CR1_RE | CR1_RXNEIE;
    NVIC_IPR(UART4_IRQ) = 0x80; // as USB: both feed the MIDI clock, so neither may interrupt the other
    NVIC_ISER(UART4_IRQ / 32) = 1u << (UART4_IRQ % 32);
}

static int data_bytes(uint8_t status) { return (status & 0xE0) == 0xC0 ? 1 : 2; } // program change, pressure: 1

int din_midi_read(uint8_t packet[4])
{
    while (rx_tail != rx_head) {
        uint8_t byte = rx_ring[rx_tail];
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
