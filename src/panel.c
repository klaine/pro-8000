// HT16K33 over bit-banged I2C. The lines go through BSS138 level shifters (Q5, Q6) with pull-ups on both
// sides, so the pins are open-drain and "high" means released.
#include "panel.h"
#include "clock.h"
#include "regs.h"

#define SCL_PIN 6
#define SDA_PIN 7

#define HT_CMD_OSC_ON     0x21
#define HT_CMD_DISPLAY_ON 0x81 // blink off
#define HT_CMD_DIM_MAX    0xEF
#define HT_ADDR_RAM       0x00
#define HT_ADDR_KEYS      0x40

static uint8_t ht_addr;

// The bus runs near 50 kHz: slow, but safe through the level shifters.
static void bit_delay(void) { delay_us(10); }

static void scl(int high) { GPIO_BSRR(GPIOB_BASE) = high ? 1u << SCL_PIN : 1u << (SCL_PIN + 16); }
static void sda(int high) { GPIO_BSRR(GPIOB_BASE) = high ? 1u << SDA_PIN : 1u << (SDA_PIN + 16); }
static int sda_in(void) { return (GPIO_IDR(GPIOB_BASE) >> SDA_PIN) & 1; }

static void i2c_start(void)
{
    sda(1); scl(1); bit_delay();
    sda(0); bit_delay();
    scl(0); bit_delay();
}

static void i2c_stop(void)
{
    sda(0); bit_delay();
    scl(1); bit_delay();
    sda(1); bit_delay();
}

// Returns 1 if the device acknowledged.
static int i2c_write(uint8_t b)
{
    for (int i = 7; i >= 0; i--) {
        sda((b >> i) & 1); bit_delay();
        scl(1); bit_delay();
        scl(0);
    }
    sda(1); bit_delay();
    scl(1); bit_delay();
    int ack = !sda_in();
    scl(0); bit_delay();
    return ack;
}

static uint8_t i2c_read(int ack)
{
    uint8_t b = 0;
    sda(1);
    for (int i = 0; i < 8; i++) {
        bit_delay();
        scl(1); bit_delay();
        b = (uint8_t)(b << 1 | sda_in());
        scl(0);
    }
    sda(!ack); bit_delay();
    scl(1); bit_delay();
    scl(0);
    sda(1); bit_delay();
    return b;
}

static int ht_command(uint8_t cmd)
{
    i2c_start();
    int ok = i2c_write((uint8_t)(ht_addr << 1)) && i2c_write(cmd);
    i2c_stop();
    return ok;
}

void panel_gpio_init(void)
{
    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOB;
    sda(1);
    scl(1);
    GPIO_OTYPER(GPIOB_BASE) |= 1u << SCL_PIN | 1u << SDA_PIN;
    GPIO_PUPDR(GPIOB_BASE) &= ~(3u << 2 * SCL_PIN | 3u << 2 * SDA_PIN);
    GPIO_MODER(GPIOB_BASE) = (GPIO_MODER(GPIOB_BASE) & ~(3u << 2 * SCL_PIN | 3u << 2 * SDA_PIN))
                           | 1u << 2 * SCL_PIN | 1u << 2 * SDA_PIN;
    // free a bus a slave might be holding after the bootloader: clock out up to 9 bits, then stop
    for (int i = 0; i < 9 && !sda_in(); i++) {
        scl(0); bit_delay();
        scl(1); bit_delay();
    }
    i2c_stop();
}

uint8_t panel_init(void)
{
    for (uint8_t a = 0x70; a <= 0x77; a++) {
        i2c_start();
        int ack = i2c_write((uint8_t)(a << 1));
        i2c_stop();
        if (ack) {
            ht_addr = a;
            break;
        }
    }
    if (!ht_addr)
        return 0;
    ht_command(HT_CMD_OSC_ON);
    ht_command(HT_CMD_DISPLAY_ON);
    ht_command(HT_CMD_DIM_MAX);
    return ht_addr;
}

void panel_write(const uint8_t ram[PANEL_RAM_BYTES])
{
    if (!ht_addr)
        return;
    i2c_start();
    if (i2c_write((uint8_t)(ht_addr << 1)) && i2c_write(HT_ADDR_RAM))
        for (int i = 0; i < PANEL_RAM_BYTES; i++)
            i2c_write(ram[i]);
    i2c_stop();
}

int panel_read_keys(uint8_t keys[PANEL_KEY_BYTES])
{
    if (!ht_addr)
        return 0;
    i2c_start();
    int ok = i2c_write((uint8_t)(ht_addr << 1)) && i2c_write(HT_ADDR_KEYS);
    if (ok) {
        i2c_start(); // repeated start
        ok = i2c_write((uint8_t)(ht_addr << 1 | 1));
    }
    if (ok)
        for (int i = 0; i < PANEL_KEY_BYTES; i++)
            keys[i] = i2c_read(i < PANEL_KEY_BYTES - 1);
    i2c_stop();
    return ok;
}
