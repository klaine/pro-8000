// First hardware test: puts the voice circuitry in a safe state, then blinks every panel LED and
// display segment once a second. Holding any panel button keeps them lit, which also proves key scanning.
#include "panel.h"
#include "regs.h"

// S&H multiplexers (74HC4051): EN1-EN9 are active low, so high disables every channel.
// EN1-3 = PD3-5, EN8 = PD6 (via U28), EN9 = PD7; EN4-7 = PB12-15 (via U28). SH0-2 = PB5, PB9, PB10.
#define EN_PINS_B  (1u << 12 | 1u << 13 | 1u << 14 | 1u << 15)
#define EN_PINS_D  (1u << 3 | 1u << 4 | 1u << 5 | 1u << 6 | 1u << 7)
#define SH_PINS_B  (1u << 5 | 1u << 9 | 1u << 10)

static volatile uint32_t ms_ticks;

void SysTick_Handler(void) { ms_ticks++; }

static void make_outputs(uint32_t port, uint32_t pins)
{
    uint32_t moder = GPIO_MODER(port);
    for (int pin = 0; pin < 16; pin++)
        if (pins & 1u << pin)
            moder = (moder & ~(3u << 2 * pin)) | 1u << 2 * pin;
    GPIO_OTYPER(port) &= ~pins;
    GPIO_MODER(port) = moder;
}

static void voices_safe(void)
{
    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOB | RCC_AHB1ENR_GPIOD;
    GPIO_BSRR(GPIOB_BASE) = EN_PINS_B | SH_PINS_B << 16; // set the level before the pin starts driving
    GPIO_BSRR(GPIOD_BASE) = EN_PINS_D;
    make_outputs(GPIOB_BASE, EN_PINS_B | SH_PINS_B);
    make_outputs(GPIOD_BASE, EN_PINS_D);
}

static void wait_ms(uint32_t ms)
{
    uint32_t start = ms_ticks;
    while (ms_ticks - start < ms)
        IWDG_KR = IWDG_KR_REFRESH;
}

int main(void)
{
    voices_safe();

    SYST_RVR = HSI_HZ / 1000 - 1;
    SYST_CVR = 0;
    SYST_CSR = 7; // processor clock, interrupt, enable

    panel_gpio_init();
    wait_ms(10);
    if (!panel_init())
        for (;;)
            IWDG_KR = IWDG_KR_REFRESH;

    uint8_t all_on[PANEL_RAM_BYTES], all_off[PANEL_RAM_BYTES] = {0};
    for (int i = 0; i < PANEL_RAM_BYTES; i++)
        all_on[i] = 0xFF;

    int lit = 0;
    for (;;) {
        uint8_t keys[PANEL_KEY_BYTES] = {0};
        int held = 0;
        if (panel_read_keys(keys))
            for (int i = 0; i < PANEL_KEY_BYTES; i++)
                held |= keys[i];
        lit = held ? 1 : !lit;
        panel_write(lit ? all_on : all_off);
        wait_ms(held ? 50 : 500);
    }
}
