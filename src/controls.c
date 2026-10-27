// Pots: three 74HC4051s share the address PM0-PM2 (PD0-PD2) and stay enabled (EN1-EN3, PD3-PD5, low):
//   U22 → VMUX1 (PC0, ADC IN10), U21 → VMUX2 (PC1, IN11), U42 → VMUX3 (PC2, IN12)
// plus CUTOFF_ADC (PC3, IN13) and NOISE_ADC (PA2, IN2) wired directly. Each pot has 100 nF at the mux input and
// each VMUX another 100 nF at the MCU, so after an address change the output needs about 4 ms (a few times
// 10 kΩ pot × 200 nF) to settle: the scan steps one address per 4 ms, 32 ms for all.
//
// Switches are plain inputs (pulled up here). The VALUE knob is an encoder without detents, quadrature on PE9 (A)
// and PE11 (B) through 74HC14s: clockwise runs AB 11 → 01 → 00 → 10, four transitions per cycle, with some contact
// bounce that cancels out in the decode. Buttons come from the HT16K33's key scan.
#include "controls.h"
#include "gpio.h"
#include "panel.h"
#include "regs.h"

#define MUX_SETTLE_MS 4
#define KEY_SCAN_MS   40
#define POT_HYSTERESIS 24 // of 4095: reports changes beyond the noise

typedef struct {
    uint32_t port;
    uint8_t pin;
    uint8_t pull_up;
} pin_t;

// Order = switch index. Names from the main-board schematic, sheet 1.
static const pin_t switch_pins[SWITCH_COUNT] = {
    {GPIOE_BASE, 0, 1},  // 0 OSC1_SAW
    {GPIOA_BASE, 8, 1},  // 1 OSC1_TRI
    {GPIOC_BASE, 4, 1},  // 2 OSC1_SQ
    {GPIOC_BASE, 5, 1},  // 3 OSC2_SAW
    {GPIOC_BASE, 6, 1},  // 4 OSC2_TRI
    {GPIOC_BASE, 7, 1},  // 5 OSC2_SQ
    {GPIOC_BASE, 8, 1},  // 6 PW_A_B (LFO → PW)
    {GPIOC_BASE, 9, 1},  // 7 F_A_B (LFO → frequency)
    {GPIOC_BASE, 11, 1}, // 8 FREQ_A (poly-mod → frequency A)
    {GPIOC_BASE, 12, 1}, // 9 LFO_SHAP
    {GPIOC_BASE, 13, 1}, // 10 LFO_FILTER
    {GPIOC_BASE, 14, 1}, // 11 FILTER (poly-mod → filter)
    {GPIOC_BASE, 15, 1}, // 12 UNISON
    {GPIOB_BASE, 1, 1},  // 13 HALF (keyboard tracking)
    {GPIOB_BASE, 2, 1},  // 14 FULL
    {GPIOD_BASE, 8, 1},  // 15 SYNC_SEL (oscillator A sync)
    {GPIOD_BASE, 9, 1},  // 16 MIDI_CH_0
    {GPIOD_BASE, 10, 1}, // 17 MIDI_CH_1
    {GPIOD_BASE, 11, 1}, // 18 MIDI_CH_2
    {GPIOD_BASE, 12, 1}, // 19 MIDI_CH_3
    {GPIOD_BASE, 13, 1}, // 20 BACKPANEL_PRESENCE
    {GPIOA_BASE, 7, 1},  // 21 BACKPANEL_PEDAL_DT
    {GPIOD_BASE, 14, 0}, // 22 SYNC IN (74HC14 U8A)
    {GPIOD_BASE, 15, 0}, // 23 SYNC IN (74HC14 U8C)
};

// HT16K33 key RAM byte and bit per button
static const struct { uint8_t byte, bit; } button_keys[BUTTON_COUNT] = {
    [BUTTON_0] = {4, 4}, [BUTTON_1] = {0, 1}, [BUTTON_2] = {0, 2}, [BUTTON_3] = {0, 3}, [BUTTON_4] = {2, 1},
    [BUTTON_5] = {2, 2}, [BUTTON_6] = {2, 3}, [BUTTON_7] = {4, 1}, [BUTTON_8] = {4, 2}, [BUTTON_9] = {4, 3},
    [BUTTON_PRESET] = {0, 4}, [BUTTON_PERF] = {0, 5}, [BUTTON_SETTINGS] = {0, 6}, [BUTTON_TUNE] = {0, 0},
    [BUTTON_SYNC_SOURCE] = {2, 4}, [BUTTON_SYNC_CLOCK] = {2, 5}, [BUTTON_REC] = {2, 0},
    [BUTTON_SEQ1] = {4, 5}, [BUTTON_SEQ2] = {4, 6}, [BUTTON_ARP_UP] = {4, 0}, [BUTTON_ARP_ASSIGN] = {0, 7},
};

#define PM_PINS (1u << 0 | 1u << 1 | 1u << 2)
#define POT_EN_PINS (1u << 3 | 1u << 4 | 1u << 5)
#define ENC_A_PIN 9
#define ENC_B_PIN 11

static uint16_t pots[POT_COUNT], reported_pots[POT_COUNT];
static uint8_t switches[SWITCH_COUNT], switch_candidate[SWITCH_COUNT];
static uint8_t keys[KEY_BYTES];
static uint32_t address, address_set_ms, last_key_scan;
static uint8_t encoder_state;
static int32_t encoder_steps;
static int first_poll = 1;

static uint16_t adc_read(uint32_t channel)
{
    ADC1_SQR3 = channel;
    ADC1_CR2 |= ADC_CR2_SWSTART;
    while (!(ADC1_SR & ADC_SR_EOC))
        ;
    return (uint16_t)ADC1_DR;
}

static int read_pin(const pin_t *p) { return (GPIO_IDR(p->port) >> p->pin) & 1; }

void controls_init(void)
{
    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOA | RCC_AHB1ENR_GPIOB | RCC_AHB1ENR_GPIOC | RCC_AHB1ENR_GPIOD | RCC_AHB1ENR_GPIOE;
    RCC_APB2ENR |= RCC_APB2ENR_ADC1;

    gpio_write(GPIOD_BASE, PM_PINS, 0);
    gpio_output(GPIOD_BASE, PM_PINS);
    gpio_write(GPIOD_BASE, POT_EN_PINS, 0); // pot muxes on; they only drive VMUX1-3
    gpio_output(GPIOD_BASE, POT_EN_PINS);

    gpio_analog(GPIOC_BASE, 1u << 0 | 1u << 1 | 1u << 2 | 1u << 3);
    gpio_analog(GPIOA_BASE, 1u << 2);
    ADC_CCR = ADC_CCR_DIV4; // 84 MHz / 4 = 21 MHz
    ADC1_SMPR1 = 0x06DB6DB6u; // sample time code 6 (144 cycles) on channels 10-18
    ADC1_SMPR2 = 0x36DB6DB6u; // and 0-9
    ADC1_CR2 = ADC_CR2_ADON;

    for (int i = 0; i < SWITCH_COUNT; i++) {
        const pin_t *p = &switch_pins[i];
        gpio_set_mode(p->port, 1u << p->pin, 0);
        GPIO_PUPDR(p->port) = (GPIO_PUPDR(p->port) & ~(3u << 2 * p->pin)) | (uint32_t)p->pull_up << 2 * p->pin;
    }
    for (volatile int i = 0; i < 1000; i++) // let the pull-ups charge the lines
        ;
    for (int i = 0; i < SWITCH_COUNT; i++)
        switches[i] = switch_candidate[i] = (uint8_t)read_pin(&switch_pins[i]);
    gpio_set_mode(GPIOE_BASE, 1u << ENC_A_PIN | 1u << ENC_B_PIN, 0);
    GPIO_PUPDR(GPIOE_BASE) = (GPIO_PUPDR(GPIOE_BASE) & ~(3u << 2 * ENC_A_PIN | 3u << 2 * ENC_B_PIN))
                           | 1u << 2 * ENC_A_PIN | 1u << 2 * ENC_B_PIN;
    encoder_state = (uint8_t)(((GPIO_IDR(GPIOE_BASE) >> ENC_A_PIN) & 1) << 1 | ((GPIO_IDR(GPIOE_BASE) >> ENC_B_PIN) & 1));
}

static void pot_changed(int i, uint16_t value, control_event_t event)
{
    pots[i] = value;
    int delta = (int)value - (int)reported_pots[i];
    if (first_poll || delta >= POT_HYSTERESIS || delta <= -POT_HYSTERESIS) {
        reported_pots[i] = value;
        if (event && !first_poll)
            event(CONTROL_POT, i, value);
    }
}

void controls_poll(uint32_t now_ms, control_event_t event)
{
    // encoder: quadrature transitions, +1 clockwise
    static const int8_t transitions[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};
    uint8_t state = (uint8_t)(((GPIO_IDR(GPIOE_BASE) >> ENC_A_PIN) & 1) << 1 | ((GPIO_IDR(GPIOE_BASE) >> ENC_B_PIN) & 1));
    if (state != encoder_state) {
        encoder_steps = transitions[encoder_state << 2 | state];
        encoder_state = state;
        if (encoder_steps && event)
            event(CONTROL_ENCODER, 0, encoder_steps);
    }

    // pots behind the muxes
    if (now_ms - address_set_ms >= MUX_SETTLE_MS) {
        pot_changed(0 * 8 + (int)address, adc_read(10), event);
        pot_changed(1 * 8 + (int)address, adc_read(11), event);
        pot_changed(2 * 8 + (int)address, adc_read(12), event);
        pot_changed(24, adc_read(13), event);
        pot_changed(25, adc_read(2), event);
        address = (address + 1) % 8;
        GPIO_BSRR(GPIOD_BASE) = (address & 7) | (~address & 7) << 16;
        address_set_ms = now_ms;
        if (address == 0)
            first_poll = 0;
    }

    // switches: a new level counts once it reads the same twice in a row
    for (int i = 0; i < SWITCH_COUNT; i++) {
        uint8_t level = (uint8_t)read_pin(&switch_pins[i]);
        if (level != switches[i] && level == switch_candidate[i]) {
            switches[i] = level;
            if (event)
                event(CONTROL_SWITCH, i, level);
        }
        switch_candidate[i] = level;
    }

    // buttons
    if (now_ms - last_key_scan >= KEY_SCAN_MS) {
        last_key_scan = now_ms;
        uint8_t now[KEY_BYTES];
        if (panel_read_keys(now))
            for (int i = 0; i < KEY_BYTES; i++)
                if (now[i] != keys[i]) {
                    uint8_t changed = now[i] ^ keys[i];
                    keys[i] = now[i];
                    if (event) {
                        event(CONTROL_KEYS, i, now[i]);
                        for (int b = 0; b < BUTTON_COUNT; b++)
                            if (button_keys[b].byte == i && changed & 1u << button_keys[b].bit)
                                event(CONTROL_BUTTON, b, (now[i] >> button_keys[b].bit) & 1);
                    }
                }
    }
}

uint16_t controls_pot(int index) { return pots[index]; }

int controls_ready(void) { return !first_poll; }

int controls_switch(int index) { return switches[index]; }

int controls_keytrack(void)
{
    if (!switches[SWITCH_KEYTRACK_FULL])
        return 2;
    return switches[SWITCH_KEYTRACK_HALF] ? 0 : 1;
}

int controls_on(switch_t s)
{
    if (s <= SWITCH_UNISON || s == SWITCH_SYNC)
        return !switches[s];
    return switches[s];
}

int controls_button(button_t button) { return (keys[button_keys[button].byte] >> button_keys[button].bit) & 1; }
