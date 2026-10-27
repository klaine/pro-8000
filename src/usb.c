// USB MIDI 1.0 device on OTG FS, in slave mode (no DMA), driven from the OTG_FS interrupt.
// Register map and init sequence: RM0390 chapter 30. Descriptors: USB Device Class Definition for MIDI Devices 1.0,
// appendix B. It reports Behringer's PRO 800 IDs and name, so the host's MIDI port keeps its name.
#include "usb.h"
#include "clock.h"
#include "gpio.h"
#include "regs.h"

#define OTG             0x50000000u
#define GOTGCTL         REG(OTG + 0x000)
#define GAHBCFG         REG(OTG + 0x008)
#define GUSBCFG         REG(OTG + 0x00C)
#define GRSTCTL         REG(OTG + 0x010)
#define GINTSTS         REG(OTG + 0x014)
#define GINTMSK         REG(OTG + 0x018)
#define GRXSTSP         REG(OTG + 0x020)
#define GRXFSIZ         REG(OTG + 0x024)
#define DIEPTXF0        REG(OTG + 0x028)
#define GCCFG           REG(OTG + 0x038)
#define DIEPTXF1        REG(OTG + 0x104)
#define DCFG            REG(OTG + 0x800)
#define DCTL            REG(OTG + 0x804)
#define DIEPMSK         REG(OTG + 0x810)
#define DOEPMSK         REG(OTG + 0x814)
#define DAINT           REG(OTG + 0x818)
#define DAINTMSK        REG(OTG + 0x81C)
#define DIEPCTL(n)      REG(OTG + 0x900 + 0x20 * (n))
#define DIEPINT(n)      REG(OTG + 0x908 + 0x20 * (n))
#define DIEPTSIZ(n)     REG(OTG + 0x910 + 0x20 * (n))
#define DOEPCTL(n)      REG(OTG + 0xB00 + 0x20 * (n))
#define DOEPINT(n)      REG(OTG + 0xB08 + 0x20 * (n))
#define DOEPTSIZ(n)     REG(OTG + 0xB10 + 0x20 * (n))
#define PCGCCTL         REG(OTG + 0xE00)
#define FIFO(n)         REG(OTG + 0x1000 * ((n) + 1))

#define GOTGCTL_BVALOEN  (1u << 6)
#define GOTGCTL_BVALOVAL (1u << 7)
#define GAHBCFG_GINT     (1u << 0)
#define GUSBCFG_PHYSEL   (1u << 6)
#define GUSBCFG_TRDT_6   (6u << 10) // turnaround for AHB ≥ 32 MHz
#define GUSBCFG_FDMOD    (1u << 30)
#define GRSTCTL_CSRST    (1u << 0)
#define GRSTCTL_RXFFLSH  (1u << 4)
#define GRSTCTL_TXFFLSH  (1u << 5)
#define GRSTCTL_TXF_ALL  (0x10u << 6)
#define GRSTCTL_AHBIDL   (1u << 31)
#define GCCFG_PWRDWN     (1u << 16)
#define GINT_RXFLVL      (1u << 4)
#define GINT_USBRST      (1u << 12)
#define GINT_ENUMDNE     (1u << 13)
#define GINT_IEPINT      (1u << 18)
#define GINT_OEPINT      (1u << 19)
#define DCFG_DSPD_FS     (3u << 0)
#define DCFG_DAD_MASK    (0x7Fu << 4)
#define DCTL_SDIS        (1u << 1)
#define DCTL_CGINAK      (1u << 8)
#define EPCTL_USBAEP     (1u << 15)
#define EPCTL_BULK       (2u << 18)
#define EPCTL_STALL      (1u << 21)
#define EPCTL_CNAK       (1u << 26)
#define EPCTL_SNAK       (1u << 27)
#define EPCTL_SD0PID     (1u << 28)
#define EPCTL_EPENA      (1u << 31)
#define EPINT_XFRC       (1u << 0)
#define DOEPINT_STUP     (1u << 3)
#define TSIZ_PKTCNT_1    (1u << 19)
#define DOEPTSIZ0_STUPCNT_3 (3u << 29)

#define RXSTS_OUT_DATA   2u
#define RXSTS_SETUP_DATA 6u

#define OTG_FS_IRQ 67

#define EP0_SIZE  64
#define MIDI_EP   1
#define MIDI_SIZE 64

#define USB_VID 0x1397 // Behringer
#define USB_PID 0x125F // PRO 800 (its bootloader is 0x1260)

static const uint8_t device_descriptor[18] = {
    18, 1, 0x00, 0x02, // USB 2.0
    0, 0, 0, EP0_SIZE, // class in the interfaces
    USB_VID & 0xFF, USB_VID >> 8, USB_PID & 0xFF, USB_PID >> 8,
    0x00, 0x01,        // device release 1.00
    1, 2, 3,           // manufacturer, product, serial strings
    1,                 // one configuration
};

static const uint8_t config_descriptor[101] = {
    9, 2, 101, 0, 2, 1, 0, 0xC0, 50, // 2 interfaces, self-powered, 100 mA
    // audio control interface, with an empty class header that points at the streaming interface
    9, 4, 0, 0, 0, 1, 1, 0, 0,
    9, 0x24, 1, 0x00, 0x01, 9, 0, 1, 1,
    // MIDI streaming interface
    9, 4, 1, 0, 2, 1, 3, 0, 0,
    7, 0x24, 1, 0x00, 0x01, 65, 0,
    6, 0x24, 2, 1, 1, 0,          // IN jack 1, embedded
    6, 0x24, 2, 2, 2, 0,          // IN jack 2, external
    9, 0x24, 3, 1, 3, 1, 2, 1, 0, // OUT jack 3, embedded, fed by jack 2
    9, 0x24, 3, 2, 4, 1, 1, 1, 0, // OUT jack 4, external, fed by jack 1
    9, 5, MIDI_EP, 2, MIDI_SIZE, 0, 0, 0, 0,        // bulk OUT
    5, 0x25, 1, 1, 1,                               //   → embedded IN jack 1
    9, 5, 0x80 | MIDI_EP, 2, MIDI_SIZE, 0, 0, 0, 0, // bulk IN
    5, 0x25, 1, 1, 3,                               //   ← embedded OUT jack 3
};

// The serial number stays the same across firmware versions, so the host (and macOS's accessory approval)
// recognises the synth as the same device. Built like ST's USB library does from the 96-bit unique ID:
// 8 hex digits of UID0 + UID2, then the top 4 of UID1. That gives 12 digits, the same format the stock
// firmware reports.
#define UID(n) REG(0x1FFF7A10u + 4u * (n))
static char serial[13];
static const char *const strings[] = {"AI & Klaine", "PRO 800", serial};

static void make_serial(void)
{
    static const char hex[] = "0123456789ABCDEF";
    uint32_t a = UID(0) + UID(2), b = UID(1);
    for (int i = 0; i < 8; i++)
        serial[i] = hex[(a >> (28 - 4 * i)) & 0xF];
    for (int i = 0; i < 4; i++)
        serial[8 + i] = hex[(b >> (28 - 4 * i)) & 0xF];
    serial[12] = 0;
}

// MIDI event packets from the host: written by the interrupt, read by the main loop
#define RX_PACKETS 64
static uint8_t rx_ring[RX_PACKETS][4];
static volatile uint32_t rx_head, rx_tail;

static uint32_t setup[2];
static volatile int configured;
static volatile int tx_busy;
static uint8_t ep0_buffer[64];

static void flush_fifos(void)
{
    GRSTCTL = GRSTCTL_TXFFLSH | GRSTCTL_TXF_ALL;
    while (GRSTCTL & GRSTCTL_TXFFLSH)
        ;
    GRSTCTL = GRSTCTL_RXFFLSH;
    while (GRSTCTL & GRSTCTL_RXFFLSH)
        ;
}

static void write_fifo(int ep, const uint8_t *data, uint32_t length)
{
    for (uint32_t i = 0; i < length; i += 4) {
        uint32_t word = 0;
        for (uint32_t b = 0; b < 4 && i + b < length; b++)
            word |= (uint32_t)data[i + b] << 8 * b;
        FIFO(ep) = word;
    }
}

static void ep0_out_arm(void)
{
    DOEPTSIZ(0) = DOEPTSIZ0_STUPCNT_3 | TSIZ_PKTCNT_1 | EP0_SIZE;
    DOEPCTL(0) |= EPCTL_EPENA | EPCTL_CNAK;
}

// Sends the data stage of a control IN transfer (or a zero-length status stage).
static void ep0_send(const uint8_t *data, uint32_t length)
{
    uint32_t packets = length == 0 ? 1 : (length + EP0_SIZE - 1) / EP0_SIZE;
    DIEPTSIZ(0) = packets << 19 | length;
    DIEPCTL(0) |= EPCTL_EPENA | EPCTL_CNAK;
    write_fifo(0, data, length);
}

static void ep0_stall(void)
{
    DIEPCTL(0) |= EPCTL_STALL;
    DOEPCTL(0) |= EPCTL_STALL;
}

static void midi_out_arm(void)
{
    DOEPTSIZ(MIDI_EP) = TSIZ_PKTCNT_1 | MIDI_SIZE;
    DOEPCTL(MIDI_EP) |= EPCTL_EPENA | EPCTL_CNAK;
}

static void configure_midi_endpoints(void)
{
    DOEPCTL(MIDI_EP) = EPCTL_USBAEP | EPCTL_BULK | EPCTL_SD0PID | MIDI_SIZE;
    DIEPCTL(MIDI_EP) = EPCTL_USBAEP | EPCTL_BULK | EPCTL_SD0PID | EPCTL_SNAK | (uint32_t)MIDI_EP << 22 | MIDI_SIZE;
    DAINTMSK |= 1u << MIDI_EP | 1u << (16 + MIDI_EP);
    tx_busy = 0;
    midi_out_arm();
    configured = 1;
}

static uint32_t string_descriptor(int index, uint8_t *out)
{
    if (index == 0) {
        out[0] = 4; out[1] = 3; out[2] = 0x09; out[3] = 0x04; // US English
        return 4;
    }
    const char *s = strings[index - 1];
    uint32_t n = 2;
    for (; *s && n < sizeof ep0_buffer - 1; s++) {
        out[n++] = (uint8_t)*s;
        out[n++] = 0;
    }
    out[0] = (uint8_t)n;
    out[1] = 3;
    return n;
}

static void handle_setup(void)
{
    uint8_t request_type = setup[0] & 0xFF;
    uint8_t request = (setup[0] >> 8) & 0xFF;
    uint16_t value = setup[0] >> 16;
    uint16_t length = setup[1] >> 16;

    if ((request_type & 0x60) != 0) { // class/vendor requests: none needed for MIDI streaming
        ep0_stall();
        return;
    }
    switch (request) {
    case 6: { // GET_DESCRIPTOR
        const uint8_t *data = 0;
        uint32_t size = 0;
        switch (value >> 8) {
        case 1: data = device_descriptor; size = sizeof device_descriptor; break;
        case 2: data = config_descriptor; size = sizeof config_descriptor; break;
        case 3:
            if ((value & 0xFF) <= sizeof strings / sizeof strings[0]) {
                size = string_descriptor(value & 0xFF, ep0_buffer);
                data = ep0_buffer;
            }
            break;
        }
        if (!data) {
            ep0_stall();
            return;
        }
        ep0_send(data, size < length ? size : length);
        return;
    }
    case 5: // SET_ADDRESS: takes effect at once; the core sends the status stage from address 0
        DCFG = (DCFG & ~DCFG_DAD_MASK) | (uint32_t)(value & 0x7F) << 4;
        ep0_send(0, 0);
        return;
    case 9: // SET_CONFIGURATION
        if (value == 1)
            configure_midi_endpoints();
        ep0_send(0, 0);
        return;
    case 8: // GET_CONFIGURATION
        ep0_buffer[0] = configured ? 1 : 0;
        ep0_send(ep0_buffer, 1);
        return;
    case 0: // GET_STATUS
        ep0_buffer[0] = (request_type & 0x1F) == 0 ? 1 : 0; // device: self-powered
        ep0_buffer[1] = 0;
        ep0_send(ep0_buffer, 2);
        return;
    case 11: // SET_INTERFACE: only alternate setting 0 exists
    case 1:  // CLEAR_FEATURE
    case 3:  // SET_FEATURE
        ep0_send(0, 0);
        return;
    default:
        ep0_stall();
    }
}

static void bus_reset(void)
{
    configured = 0;
    tx_busy = 0;
    DCFG &= ~DCFG_DAD_MASK;
    DOEPCTL(MIDI_EP) = 0;
    DIEPCTL(MIDI_EP) = 0;
    flush_fifos();
    DAINTMSK = 1u << 0 | 1u << 16; // EP0 IN and OUT
    DOEPMSK = EPINT_XFRC | DOEPINT_STUP;
    DIEPMSK = EPINT_XFRC;
    ep0_out_arm();
}

static void read_packet(uint32_t status)
{
    uint32_t ep = status & 0xF;
    uint32_t count = (status >> 4) & 0x7FF;
    uint32_t kind = (status >> 17) & 0xF;
    uint32_t words = (count + 3) / 4;

    if (kind == RXSTS_SETUP_DATA) {
        setup[0] = FIFO(0);
        setup[1] = FIFO(0);
        return;
    }
    if (kind != RXSTS_OUT_DATA) {
        for (uint32_t i = 0; i < words; i++)
            (void)FIFO(0);
        return;
    }
    for (uint32_t i = 0; i < words; i++) {
        uint32_t word = FIFO(0);
        if (ep != MIDI_EP || 4 * i + 4 > count || (word & 0x0F) == 0) // skip padding and empty events
            continue;
        uint32_t next = (rx_head + 1) % RX_PACKETS;
        if (next == rx_tail)
            continue; // full: drop
        for (int b = 0; b < 4; b++)
            rx_ring[rx_head][b] = (uint8_t)(word >> 8 * b);
        rx_head = next;
    }
}

void OTG_FS_IRQHandler(void)
{
    uint32_t status = GINTSTS & GINTMSK;

    if (status & GINT_USBRST) {
        GINTSTS = GINT_USBRST;
        bus_reset();
    }
    if (status & GINT_ENUMDNE) {
        GINTSTS = GINT_ENUMDNE;
        DIEPCTL(0) &= ~3u; // EP0 max packet 64
        DCTL |= DCTL_CGINAK;
    }
    while (GINTSTS & GINT_RXFLVL)
        read_packet(GRXSTSP);
    if (status & GINT_OEPINT) {
        uint32_t eps = DAINT >> 16;
        if (eps & 1) {
            uint32_t flags = DOEPINT(0);
            DOEPINT(0) = flags;
            if (flags & DOEPINT_STUP)
                handle_setup();
            ep0_out_arm();
        }
        if (eps & 1u << MIDI_EP) {
            uint32_t flags = DOEPINT(MIDI_EP);
            DOEPINT(MIDI_EP) = flags;
            if (flags & EPINT_XFRC)
                midi_out_arm();
        }
    }
    if (status & GINT_IEPINT) {
        uint32_t eps = DAINT & 0xFFFF;
        if (eps & 1)
            DIEPINT(0) = DIEPINT(0);
        if (eps & 1u << MIDI_EP) {
            uint32_t flags = DIEPINT(MIDI_EP);
            DIEPINT(MIDI_EP) = flags;
            if (flags & EPINT_XFRC)
                tx_busy = 0;
        }
    }
}

void usb_init(void)
{
    make_serial();
    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOA;
    RCC_AHB2ENR |= RCC_AHB2ENR_OTGFS;
    gpio_alternate(GPIOA_BASE, 1u << 11 | 1u << 12, 10);

    while (!(GRSTCTL & GRSTCTL_AHBIDL))
        ;
    GRSTCTL = GRSTCTL_CSRST;
    while (GRSTCTL & GRSTCTL_CSRST)
        ;
    while (!(GRSTCTL & GRSTCTL_AHBIDL))
        ;

    // no VBUS sensing (PA9 is wired but not needed): power the transceiver and force the B-session valid
    GCCFG = GCCFG_PWRDWN;
    GOTGCTL |= GOTGCTL_BVALOEN | GOTGCTL_BVALOVAL;
    GUSBCFG = GUSBCFG_FDMOD | GUSBCFG_TRDT_6 | GUSBCFG_PHYSEL;
    delay_us(25000); // forced device mode takes up to 25 ms

    DCFG = DCFG_DSPD_FS;
    PCGCCTL = 0;
    // FIFO RAM is 320 words: 128 receive, 64 for EP0 IN, 64 for the MIDI IN endpoint
    GRXFSIZ = 128;
    DIEPTXF0 = 64u << 16 | 128;
    DIEPTXF1 = 64u << 16 | 192;
    flush_fifos();

    GINTSTS = 0xFFFFFFFFu;
    GINTMSK = GINT_USBRST | GINT_ENUMDNE | GINT_RXFLVL | GINT_IEPINT | GINT_OEPINT;
    GAHBCFG = GAHBCFG_GINT;
    NVIC_IPR(OTG_FS_IRQ) = 0x80; // below the tuning edge interrupt
    NVIC_ISER(OTG_FS_IRQ / 32) = 1u << (OTG_FS_IRQ % 32);

    DCTL &= ~DCTL_SDIS; // connect
}

int usb_midi_read(uint8_t packet[4])
{
    if (rx_tail == rx_head)
        return 0;
    for (int b = 0; b < 4; b++)
        packet[b] = rx_ring[rx_tail][b];
    rx_tail = (rx_tail + 1) % RX_PACKETS;
    return 1;
}

int usb_midi_write(const uint8_t *packets, int count)
{
    if (!configured || tx_busy || count <= 0 || count > MIDI_SIZE / 4)
        return 0;
    NVIC_ICER(OTG_FS_IRQ / 32) = 1u << (OTG_FS_IRQ % 32);
    tx_busy = 1;
    DIEPTSIZ(MIDI_EP) = TSIZ_PKTCNT_1 | (uint32_t)count * 4;
    DIEPCTL(MIDI_EP) |= EPCTL_EPENA | EPCTL_CNAK;
    write_fifo(MIDI_EP, packets, (uint32_t)count * 4);
    NVIC_ISER(OTG_FS_IRQ / 32) = 1u << (OTG_FS_IRQ % 32);
    return 1;
}

void usb_disconnect(void)
{
    NVIC_ICER(OTG_FS_IRQ / 32) = 1u << (OTG_FS_IRQ % 32);
    DCTL |= DCTL_SDIS;
    configured = 0;
}
