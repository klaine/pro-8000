// DAC80501 in SPI mode (SPI2C tied low): SCLK = PC10, SDIN = PB0, SYNC = PA15, bit-banged at about 500 kHz.
// Anything faster has garbled it so far: hardware SPI3 from 0.66 MHz up (0.0.5, 0.0.6) and bit-banging at 1 MHz
// (0.5.2). (0.0.2's "1 MHz" was 500 kHz: two 1 us half-bits.) The DAC is rated far faster, so the cause is still
// open: edge timing, slew, something on the board. dac_configure() lets the host try other buses at run time
// (`pro800 dac-test`); a reboot always comes back to the default below.
#include "dac.h"
#include "clock.h"
#include "gpio.h"
#include "regs.h"

#define SCLK_PORT GPIOC_BASE
#define SCLK_PIN  (1u << 10)
#define SDIN_PORT GPIOB_BASE
#define SDIN_PIN  (1u << 0)
#define SYNC_PORT GPIOA_BASE
#define SYNC_PIN  (1u << 15)
#define SCLK_AF   6 // SPI3_SCK
#define SDIN_AF   7 // SPI3_MOSI

#define DAC80501_TRIGGER 0x05
#define DAC80501_GAIN    0x04
#define DAC80501_CONFIG  0x03
#define DAC80501_DATA    0x08

#define DAC80501_SOFT_RESET 0x000Au
// Its supply is 3.3 V, too low for the undivided 2.5 V reference (that raises the reference alarm and forces
// 0 V out), so: reference / 2, buffer gain 2, giving 0-2.5 V; U47A doubles that.
#define DAC80501_REF_DIV_GAIN2 0x0101u

// 0.5 MHz: the board takes nothing above 0.6 (the dac-test sweep, NOTES). No wait around SYNC: it works without, and
// the DAC's own minimum is 20 ns.
const dac_config_t dac_default = {.bus = DAC_BUS_BITBANG, .mode = 0, .high = 168, .low = 168, .speed = 0, .sync_us = 0};
static dac_config_t config = {.bus = DAC_BUS_BITBANG, .mode = 0, .high = 168, .low = 168, .speed = 0, .sync_us = 0};

// 24-bit frame: command byte then 16 data bits, MSB first. The DAC latches SDIN on the falling edge of SCLK.
// Mode 0 changes SDIN as SCLK rises (setup = the high time), mode 1 just after SCLK falls (setup = the low time,
// and the bit is stable through the whole next edge). `before_update` runs before the last two bits (dac16_write()).
// Each half-bit ends at a deadline counted from the cycle counter, so the GPIO writes and the loop add nothing to
// the frame: 24 × (high + low) cycles, 48 µs at the default.
static void wait_until(uint32_t deadline)
{
    while ((int32_t)(DWT_CYCCNT - deadline) < 0)
        ;
}

static void bitbang_frame(uint32_t frame, void (*before_update)(void))
{
    uint32_t high = config.high, low = config.low, mode = config.mode;
    uint32_t t = DWT_CYCCNT;
    for (int bit = 23; bit >= 0; bit--) {
        if (bit == 1 && before_update) {
            before_update();
            t = DWT_CYCCNT;
        }
        if (mode == 0) {
            gpio_write(SCLK_PORT, SCLK_PIN, 1);
            gpio_write(SDIN_PORT, SDIN_PIN, (frame >> bit) & 1);
            wait_until(t += high);
            gpio_write(SCLK_PORT, SCLK_PIN, 0);
            wait_until(t += low);
        } else {
            gpio_write(SDIN_PORT, SDIN_PIN, (frame >> bit) & 1);
            wait_until(t += low);
            gpio_write(SCLK_PORT, SCLK_PIN, 1);
            wait_until(t += high);
            gpio_write(SCLK_PORT, SCLK_PIN, 0);
        }
    }
}

static void spi_frame(uint32_t frame)
{
    for (int shift = 16; shift >= 0; shift -= 8) {
        while (!(SPI3_SR & SPI_SR_TXE))
            ;
        SPI3_DR = (frame >> shift) & 0xFF;
        while (!(SPI3_SR & SPI_SR_RXNE)) // one byte out before the next goes in, so SYNC can't rise early
            ;
        (void)SPI3_DR;
    }
    while (SPI3_SR & SPI_SR_BSY)
        ;
}

static void dac80501_write(uint8_t command, uint16_t data, void (*before_update)(void))
{
    uint32_t frame = (uint32_t)command << 16 | data;
    if (config.bus == DAC_BUS_SPI && before_update)
        before_update();
    gpio_write(SYNC_PORT, SYNC_PIN, 0);
    if (config.sync_us)
        delay_us(config.sync_us);
    if (config.bus == DAC_BUS_SPI)
        spi_frame(frame);
    else
        bitbang_frame(frame, before_update);
    gpio_write(SYNC_PORT, SYNC_PIN, 1);
    if (config.sync_us)
        delay_us(config.sync_us);
}

static void set_speed(uint32_t port, uint32_t pin_mask, uint32_t speed)
{
    for (int pin = 0; pin < 16; pin++)
        if (pin_mask & 1u << pin)
            GPIO_OSPEEDR(port) = (GPIO_OSPEEDR(port) & ~(3u << 2 * pin)) | speed << 2 * pin;
}

// Pins and SPI3 for `config`, then the DAC's registers again through that bus
static void apply(void)
{
    SPI3_CR1 = 0; // off (SPE = 0) while the pins change
    gpio_write(SYNC_PORT, SYNC_PIN, 1);
    gpio_write(SCLK_PORT, SCLK_PIN, config.bus == DAC_BUS_SPI && (config.mode & 2)); // SPI: idle at CPOL
    gpio_write(SDIN_PORT, SDIN_PIN, 0);
    gpio_output(SYNC_PORT, SYNC_PIN);
    set_speed(SCLK_PORT, SCLK_PIN, config.speed);
    set_speed(SDIN_PORT, SDIN_PIN, config.speed);
    set_speed(SYNC_PORT, SYNC_PIN, config.speed);
    if (config.bus == DAC_BUS_SPI) {
        RCC_APB1ENR |= RCC_APB1ENR_SPI3;
        // mode bit 0 = CPHA, bit 1 = CPOL; the DAC wants the data stable at the falling edge: modes 1 and 2
        SPI3_CR1 = SPI_CR1_MSTR | SPI_CR1_SSM | SPI_CR1_SSI | (config.mode & 3u) | (config.high & 7u) << SPI_CR1_BR_SHIFT;
        SPI3_CR1 |= SPI_CR1_SPE;
        gpio_alternate(SCLK_PORT, SCLK_PIN, SCLK_AF);
        gpio_alternate(SDIN_PORT, SDIN_PIN, SDIN_AF);
        set_speed(SCLK_PORT, SCLK_PIN, config.speed); // gpio_alternate sets "fast"
        set_speed(SDIN_PORT, SDIN_PIN, config.speed);
    } else {
        gpio_output(SCLK_PORT, SCLK_PIN);
        gpio_output(SDIN_PORT, SDIN_PIN);
    }

    dac80501_write(DAC80501_TRIGGER, DAC80501_SOFT_RESET, 0);
    delay_us(100);
    dac16_setup();
    dac80501_write(DAC80501_DATA, 0, 0);
}

// The DAC80501 can't be read back, and a garbled setup frame at boot once left it at another gain (0.8.4: the
// oscillators and filters off until a reboot), so sh_refresh_dac_setup() sends these again every so often
void dac16_setup(void)
{
    dac80501_write(DAC80501_CONFIG, 0, 0); // internal reference and DAC powered
    dac80501_write(DAC80501_GAIN, DAC80501_REF_DIV_GAIN2, 0);
}

void dac16_register(uint8_t command, uint16_t value) { dac80501_write(command, value, 0); }

void dac_init(void)
{
    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOA | RCC_AHB1ENR_GPIOB | RCC_AHB1ENR_GPIOC;
    RCC_APB1ENR |= RCC_APB1ENR_DAC;
    apply();

    gpio_analog(GPIOA_BASE, 1u << 4 | 1u << 5);
    DAC_DHR12R1 = 0;
    DAC_DHR12R2 = 0;
    DAC_CR = DAC_CR_EN1 | DAC_CR_EN2; // output buffers on (BOFF = 0)
}

int dac_configure(const dac_config_t *c)
{
    if (c->bus > DAC_BUS_SPI || c->mode > 3 || (c->bus == DAC_BUS_BITBANG && (c->mode > 1 || !c->high || !c->low))
        || c->speed > 3 || c->sync_us > 20)
        return 0;
    config = *c;
    apply();
    return 1;
}

const dac_config_t *dac_config(void) { return &config; }

void dac16_write(uint16_t value, void (*before_update)(void)) { dac80501_write(DAC80501_DATA, value, before_update); }

void dac12_write(int channel, uint16_t value)
{
    if (channel == 1)
        DAC_DHR12R1 = value >> 4;
    else
        DAC_DHR12R2 = value >> 4;
}
