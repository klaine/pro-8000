// S&H refresh. Each CV has a 74HC4051 (address SH0-SH2 shared by all, active-low enable EN1-EN9) that routes
// one DAC onto a 10 nF hold capacitor buffered by a TL064. Mapping from the main-board schematic, sheets 2-3:
//   EN5 OSC_nA, EN6 OSC_nB, EN7 FIL_n   ← DAC80501, address = voice - 1
//   EN8 AMP_n                           ← on-chip DAC2, address = voice - 1
//   EN9 PMOD_OSC_B, VOL_A, VOL_B, M_VOL, NOISE, EXT_FIL (addresses 0-5) ← on-chip DAC1
//   EN4 PW_A, PW_B, RES (addresses 0-2) ← on-chip DAC1
// EN1-EN3 select the pot multiplexers and stay high here.
//
// Pipelined: a channel samples (its enable low) until something it samples is about to change: the DAC80501 two bits
// before the end of its next frame, an on-chip DAC before its next value, or the address. So the 18 us of settle and
// sample hide inside the next 48 us frame. The three DACs feed different muxes, so at each DAC80501 channel's address
// the VCA there (DAC2) samples alongside, and a global CV there (DAC1) when one is due: they cost nothing extra.
#include "sh.h"
#include "clock.h"
#include "dac.h"
#include "gpio.h"

#define EN_PINS_B (1u << 12 | 1u << 13 | 1u << 14 | 1u << 15) // EN4-EN7
#define EN_PINS_D (1u << 3 | 1u << 4 | 1u << 5 | 1u << 6 | 1u << 7) // EN1-EN3, EN8, EN9
#define SH_PINS_B (1u << 5 | 1u << 9 | 1u << 10) // SH0, SH1, SH2
#define GATE_PINS_E (1u << 7 | 1u << 8 | 1u << 10 | 1u << 12 | 1u << 13 | 1u << 14 | 1u << 15)

// DAC80501 settling plus the U47A/U16 buffers, then the time the 4051 needs to charge 10 nF to within 16 bits, in
// tenths of a microsecond: together, the least a channel samples after its DAC changed. The host can change them
// (sh_set_timing()) to find out how short they can be.
#define DAC_SETTLE_DEFAULT 30
#define SAMPLE_DEFAULT     60
static uint32_t settle_tenths = DAC_SETTLE_DEFAULT, sample_tenths = SAMPLE_DEFAULT;

static uint32_t tenths_to_cycles(uint32_t tenths) { return tenths * (system_hz / 1000u) / 10000u; }

typedef struct {
    uint32_t en_port;
    uint32_t en_pin;
} mux_t;

static const mux_t mux_osc_a = {GPIOB_BASE, 1u << 13};  // EN5, DAC80501
static const mux_t mux_osc_b = {GPIOB_BASE, 1u << 14};  // EN6, DAC80501
static const mux_t mux_fil = {GPIOB_BASE, 1u << 15};    // EN7, DAC80501
static const mux_t mux_amp = {GPIOD_BASE, 1u << 6};     // EN8, DAC2
static const mux_t mux_global = {GPIOD_BASE, 1u << 7};  // EN9, DAC1
static const mux_t mux_pw_res = {GPIOB_BASE, 1u << 12}; // EN4, DAC1

// The DAC1 channels: EN9's at addresses 0-5, then EN4's at 0-2
#define GLOBALS 9
static int global_address(int g) { return g < 6 ? g : g - 6; }
static const mux_t *global_mux(int g) { return g < 6 ? &mux_global : &mux_pw_res; }
static cv_t global_cv(int g) { return g < 6 ? (cv_t)(CV_PMOD_OSC_B + g) : (cv_t)(CV_PW_A + g - 6); }

static const uint32_t gate_pins[GATE_COUNT] = {
    1u << 7,  // SAW_A PE7
    1u << 8,  // TRI_A PE8
    1u << 10, // SYNC PE10
    1u << 12, // SAW_B PE12
    1u << 13, // TRI_B PE13
    1u << 14, // PMOD_FA PE14
    1u << 15, // PMOD_FIL PE15
};

static uint16_t cvs[CV_COUNT];
static unsigned globals_due = (1u << GLOBALS) - 1; // not yet written since the last sh_refresh_global()

// The channels sampling now: their enables (low) on ports B and D, since `opened` (cycle counter)
static uint32_t open_b, open_d, opened;

static void select_address(uint32_t address)
{
    GPIO_BSRR(GPIOB_BASE) = (address & 1 ? 1u << 5 : 1u << (5 + 16))
                          | (address & 2 ? 1u << 9 : 1u << (9 + 16))
                          | (address & 4 ? 1u << 10 : 1u << (10 + 16));
}

// Ends the sample in progress, once it has run settle + sample since its DACs changed
static void hold(void)
{
    if (!(open_b | open_d))
        return;
    uint32_t least = tenths_to_cycles(settle_tenths + sample_tenths);
    while (DWT_CYCCNT - opened < least)
        ;
    GPIO_BSRR(GPIOB_BASE) = open_b; // high: the 4051 lets go, the capacitor holds
    GPIO_BSRR(GPIOD_BASE) = open_d;
    open_b = open_d = 0;
}

static void add(const mux_t *mux)
{
    if (mux->en_port == GPIOB_BASE)
        open_b |= mux->en_pin;
    else
        open_d |= mux->en_pin;
}

// Starts the channels add() collected sampling (their DACs have just been set)
static void start(void)
{
    GPIO_BSRR(GPIOB_BASE) = open_b << 16;
    GPIO_BSRR(GPIOD_BASE) = open_d << 16;
    opened = DWT_CYCCNT;
}

// At `address` (the previous sample held), the VCA there on DAC2 and, unless `vca_only`, a global CV there that is
// due on DAC1
static void add_on_chip(uint32_t address, int vca_only)
{
    dac12_write(2, cvs[CV_AMP1 + address]);
    add(&mux_amp);
    for (int g = 0; g < GLOBALS && !vca_only; g++)
        if (globals_due >> g & 1 && (uint32_t)global_address(g) == address) {
            dac12_write(1, cvs[global_cv(g)]);
            add(global_mux(g));
            globals_due &= ~(1u << g);
            break;
        }
}

// A DAC80501 channel (oscillator or filter of the voice at `address`): its frame clocks in while the channel before
// still samples, then it samples with the VCA (and maybe a global CV) at its address until the next one.
static void sample16(const mux_t *mux, uint32_t address, uint16_t value)
{
    dac16_write(value, hold);
    hold(); // (the SPI probe bus has held already)
    select_address(address);
    add(mux);
    add_on_chip(address, 0);
    start();
}

void sh_safe(void)
{
    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOB | RCC_AHB1ENR_GPIOD;
    gpio_write(GPIOB_BASE, EN_PINS_B, 1);
    gpio_write(GPIOB_BASE, SH_PINS_B, 0);
    gpio_write(GPIOD_BASE, EN_PINS_D, 1);
    gpio_output(GPIOB_BASE, EN_PINS_B | SH_PINS_B);
    gpio_output(GPIOD_BASE, EN_PINS_D);
}

void sh_init(void)
{
    sh_safe();

    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOE;
    gpio_write(GPIOE_BASE, GATE_PINS_E, 0);
    gpio_output(GPIOE_BASE, GATE_PINS_E);

    dac_init();
}

void sh_set(cv_t cv, uint16_t value) { cvs[cv] = value; }

void sh_set_timing(uint32_t settle, uint32_t sample)
{
    settle_tenths = settle ? settle : DAC_SETTLE_DEFAULT;
    sample_tenths = sample ? sample : SAMPLE_DEFAULT;
}

void sh_timing(uint32_t *settle, uint32_t *sample)
{
    *settle = settle_tenths;
    *sample = sample_tenths;
}

void sh_hold(void) { hold(); }

void sh_gate(gate_t gate, int on) { gpio_write(GPIOE_BASE, gate_pins[gate], on); }

void sh_refresh_pitch(unsigned voices)
{
    for (uint32_t v = 0; v < VOICE_COUNT; v++)
        if (voices >> v & 1) {
            sample16(&mux_osc_a, v, cvs[CV_OSC_A1 + v]);
            sample16(&mux_osc_b, v, cvs[CV_OSC_B1 + v]);
        }
}

void sh_refresh_cutoff(unsigned voices)
{
    for (uint32_t v = 0; v < VOICE_COUNT; v++)
        if (voices >> v & 1)
            sample16(&mux_fil, v, cvs[CV_FIL1 + v]);
}

void sh_refresh_vca(unsigned skip)
{
    for (uint32_t v = 0; v < VOICE_COUNT; v++)
        if (!(skip >> v & 1)) {
            hold();
            select_address(v);
            add_on_chip(v, 0);
            start();
        }
}

void sh_refresh_global(void)
{
    for (int g = 0; g < GLOBALS; g++)
        if (globals_due >> g & 1) {
            uint32_t address = (uint32_t)global_address(g);
            hold();
            select_address(address);
            dac12_write(1, cvs[global_cv(g)]);
            add(global_mux(g));
            add_on_chip(address, 1); // the VCA there too, while we're at it
            start();
            globals_due &= ~(1u << g);
        }
    globals_due = (1u << GLOBALS) - 1;
}

void sh_refresh(void)
{
    sh_refresh_pitch(0xFF);
    sh_refresh_cutoff(0xFF);
    sh_refresh_global();
}
