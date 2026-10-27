// DAC80501 in SPI mode (SPI2C tied low): SCLK = PC10, SDIN = PB0, SYNC = PA15, bit-banged at about 250 kHz.
// These are SPI3's pins, but hardware SPI3 only worked at its slowest clock (APB1 / 128): diagnostics 0.0.5 and
// 0.0.6 got wrong output at 0.66-10.5 MHz while bit-banging at the same core clock worked. Cause unknown so far.
#include "dac.h"
#include "clock.h"
#include "gpio.h"

#define SCLK_PORT GPIOC_BASE
#define SCLK_PIN  (1u << 10)
#define SDIN_PORT GPIOB_BASE
#define SDIN_PIN  (1u << 0)
#define SYNC_PORT GPIOA_BASE
#define SYNC_PIN  (1u << 15)

#define DAC80501_TRIGGER 0x05
#define DAC80501_GAIN    0x04
#define DAC80501_CONFIG  0x03
#define DAC80501_DATA    0x08

#define DAC80501_SOFT_RESET 0x000Au
// Its supply is 3.3 V, too low for the undivided 2.5 V reference (that raises the reference alarm and forces
// 0 V out), so: reference / 2, buffer gain 2, giving 0-2.5 V; U47A doubles that.
#define DAC80501_REF_DIV_GAIN2 0x0101u

// 24-bit frame: command byte then 16 data bits, MSB first. SDIN changes while SCLK is high; the DAC latches on
// the falling edge.
static void dac80501_write(uint8_t command, uint16_t data)
{
    uint32_t frame = (uint32_t)command << 16 | data;
    gpio_write(SYNC_PORT, SYNC_PIN, 0);
    delay_us(1);
    for (int bit = 23; bit >= 0; bit--) {
        gpio_write(SCLK_PORT, SCLK_PIN, 1);
        gpio_write(SDIN_PORT, SDIN_PIN, (frame >> bit) & 1);
        delay_us(1);
        gpio_write(SCLK_PORT, SCLK_PIN, 0);
        delay_us(1);
    }
    gpio_write(SYNC_PORT, SYNC_PIN, 1);
    delay_us(1);
}

void dac_init(void)
{
    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOA | RCC_AHB1ENR_GPIOB | RCC_AHB1ENR_GPIOC;
    RCC_APB1ENR |= RCC_APB1ENR_DAC;

    gpio_write(SYNC_PORT, SYNC_PIN, 1);
    gpio_write(SCLK_PORT, SCLK_PIN, 0);
    gpio_write(SDIN_PORT, SDIN_PIN, 0);
    gpio_output(SYNC_PORT, SYNC_PIN);
    gpio_output(SCLK_PORT, SCLK_PIN);
    gpio_output(SDIN_PORT, SDIN_PIN);

    dac80501_write(DAC80501_TRIGGER, DAC80501_SOFT_RESET);
    delay_us(100);
    dac80501_write(DAC80501_CONFIG, 0); // internal reference and DAC powered
    dac80501_write(DAC80501_GAIN, DAC80501_REF_DIV_GAIN2);
    dac80501_write(DAC80501_DATA, 0);

    gpio_analog(GPIOA_BASE, 1u << 4 | 1u << 5);
    DAC_DHR12R1 = 0;
    DAC_DHR12R2 = 0;
    DAC_CR = DAC_CR_EN1 | DAC_CR_EN2; // output buffers on (BOFF = 0)
}

void dac16_write(uint16_t value) { dac80501_write(DAC80501_DATA, value); }

void dac12_write(int channel, uint16_t value)
{
    if (channel == 1)
        DAC_DHR12R1 = value >> 4;
    else
        DAC_DHR12R2 = value >> 4;
}
