// DAC80501 in SPI mode (SPI2C tied low): SCLK = PC10, SDIN = PB0, SYNC = PA15. Bit-banged for now; the pins
// are SPI3's, so hardware SPI can take over once its alternate-function mapping is confirmed.
#include "dac.h"
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

// 24-bit frame: command byte then 16 data bits, MSB first, latched on SCLK falling edges.
static void dac80501_write(uint8_t command, uint16_t data)
{
    uint32_t frame = (uint32_t)command << 16 | data;
    gpio_write(SYNC_PORT, SYNC_PIN, 0);
    for (int bit = 23; bit >= 0; bit--) {
        gpio_write(SCLK_PORT, SCLK_PIN, 1);
        gpio_write(SDIN_PORT, SDIN_PIN, (frame >> bit) & 1);
        gpio_write(SCLK_PORT, SCLK_PIN, 0);
    }
    gpio_write(SYNC_PORT, SYNC_PIN, 1);
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
    for (volatile int i = 0; i < 2000; i++) // reset takes a few µs
        ;
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
