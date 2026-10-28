// S&H refresh. Each CV has a 74HC4051 (address SH0-SH2 shared by all, active-low enable EN1-EN9) that routes
// one DAC onto a 10 nF hold capacitor buffered by a TL064. Mapping from the main-board schematic, sheets 2-3:
//   EN5 OSC_nA, EN6 OSC_nB, EN7 FIL_n   ← DAC80501, address = voice - 1
//   EN8 AMP_n                           ← on-chip DAC2, address = voice - 1
//   EN9 PMOD_OSC_B, VOL_A, VOL_B, M_VOL, NOISE, EXT_FIL (addresses 0-5) ← on-chip DAC1
//   EN4 PW_A, PW_B, RES (addresses 0-2) ← on-chip DAC1
// EN1-EN3 select the pot multiplexers and stay high here.
#include "sh.h"
#include "clock.h"
#include "dac.h"
#include "gpio.h"

#define EN_PINS_B (1u << 12 | 1u << 13 | 1u << 14 | 1u << 15) // EN4-EN7
#define EN_PINS_D (1u << 3 | 1u << 4 | 1u << 5 | 1u << 6 | 1u << 7) // EN1-EN3, EN8, EN9
#define SH_PINS_B (1u << 5 | 1u << 9 | 1u << 10) // SH0, SH1, SH2
#define GATE_PINS_E (1u << 7 | 1u << 8 | 1u << 10 | 1u << 12 | 1u << 13 | 1u << 14 | 1u << 15)

// DAC80501 settling plus the U47A/U16 buffers, then the time the 4051 needs to charge 10 nF to within 16 bits, in
// tenths of a microsecond. The host can change them (sh_set_timing()) to find out how short they can be.
#define DAC_SETTLE_DEFAULT 60
#define SAMPLE_DEFAULT     120
static uint32_t settle_tenths = DAC_SETTLE_DEFAULT, sample_tenths = SAMPLE_DEFAULT;

static uint32_t tenths_to_cycles(uint32_t tenths) { return tenths * (system_hz / 1000u) / 10000u; }

typedef enum { SRC_DAC16, SRC_DAC12_1, SRC_DAC12_2 } source_t;

typedef struct {
    uint32_t en_port;
    uint32_t en_pin;
    source_t source;
} mux_t;

static const mux_t mux_osc_a = {GPIOB_BASE, 1u << 13, SRC_DAC16};   // EN5
static const mux_t mux_osc_b = {GPIOB_BASE, 1u << 14, SRC_DAC16};   // EN6
static const mux_t mux_fil = {GPIOB_BASE, 1u << 15, SRC_DAC16};     // EN7
static const mux_t mux_amp = {GPIOD_BASE, 1u << 6, SRC_DAC12_2};    // EN8
static const mux_t mux_global = {GPIOD_BASE, 1u << 7, SRC_DAC12_1}; // EN9
static const mux_t mux_pw_res = {GPIOB_BASE, 1u << 12, SRC_DAC12_1}; // EN4

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

static void select_address(uint32_t address)
{
    GPIO_BSRR(GPIOB_BASE) = (address & 1 ? 1u << 5 : 1u << (5 + 16))
                          | (address & 2 ? 1u << 9 : 1u << (9 + 16))
                          | (address & 4 ? 1u << 10 : 1u << (10 + 16));
}

static void sample(const mux_t *mux, uint32_t address, uint16_t value)
{
    switch (mux->source) {
    case SRC_DAC16: dac16_write(value); break;
    case SRC_DAC12_1: dac12_write(1, value); break;
    case SRC_DAC12_2: dac12_write(2, value); break;
    }
    select_address(address);
    delay_cycles(tenths_to_cycles(settle_tenths));
    gpio_write(mux->en_port, mux->en_pin, 0);
    delay_cycles(tenths_to_cycles(sample_tenths));
    gpio_write(mux->en_port, mux->en_pin, 1);
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

void sh_gate(gate_t gate, int on) { gpio_write(GPIOE_BASE, gate_pins[gate], on); }

void sh_refresh_pitch(unsigned voices)
{
    for (uint32_t v = 0; v < VOICE_COUNT; v++)
        if (voices >> v & 1) {
            sample(&mux_osc_a, v, cvs[CV_OSC_A1 + v]);
            sample(&mux_osc_b, v, cvs[CV_OSC_B1 + v]);
        }
}

void sh_refresh_cutoff(unsigned voices)
{
    for (uint32_t v = 0; v < VOICE_COUNT; v++)
        if (voices >> v & 1)
            sample(&mux_fil, v, cvs[CV_FIL1 + v]);
}

void sh_refresh_vca(void)
{
    for (uint32_t v = 0; v < VOICE_COUNT; v++)
        sample(&mux_amp, v, cvs[CV_AMP1 + v]);
}

void sh_refresh_global(void)
{
    for (uint32_t a = 0; a <= CV_EXT_FIL - CV_PMOD_OSC_B; a++)
        sample(&mux_global, a, cvs[CV_PMOD_OSC_B + a]);
    for (uint32_t a = 0; a <= CV_RESONANCE - CV_PW_A; a++)
        sample(&mux_pw_res, a, cvs[CV_PW_A + a]);
}

void sh_refresh(void)
{
    sh_refresh_pitch(0xFF);
    sh_refresh_cutoff(0xFF);
    sh_refresh_vca();
    sh_refresh_global();
}
