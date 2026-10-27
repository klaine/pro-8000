// Bit-banged SPI mode 0, about 250 kHz: WP = PE1, SCK = PE2, HOLD = PE3, CS = PE4, MISO (SO) = PE5,
// MOSI (SI) = PE6. Only READ (03) and RDSR (05) are ever sent; WP stays low as well.
#include "eeprom.h"
#include "clock.h"
#include "gpio.h"

#define WP   (1u << 1)
#define SCK  (1u << 2)
#define HOLD (1u << 3)
#define CS   (1u << 4)
#define MISO_PIN 5
#define MOSI (1u << 6)

#define CMD_READ 0x03
#define CMD_RDSR 0x05

static uint8_t transfer(uint8_t out)
{
    uint8_t in = 0;
    for (int bit = 7; bit >= 0; bit--) {
        gpio_write(GPIOE_BASE, MOSI, (out >> bit) & 1);
        delay_us(1);
        gpio_write(GPIOE_BASE, SCK, 1); // the chip samples SI and drives SO on this edge's pair
        in = (uint8_t)(in << 1 | ((GPIO_IDR(GPIOE_BASE) >> MISO_PIN) & 1));
        delay_us(1);
        gpio_write(GPIOE_BASE, SCK, 0);
    }
    return in;
}

void eeprom_init(void)
{
    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOE;
    gpio_write(GPIOE_BASE, CS | HOLD, 1);
    gpio_write(GPIOE_BASE, SCK | MOSI | WP, 0);
    gpio_output(GPIOE_BASE, WP | SCK | HOLD | CS | MOSI);
    gpio_set_mode(GPIOE_BASE, 1u << MISO_PIN, 0);
}

void eeprom_read(uint32_t address, uint8_t *data, uint32_t length)
{
    gpio_write(GPIOE_BASE, CS, 0);
    delay_us(1);
    transfer(CMD_READ);
    transfer((uint8_t)(address >> 16));
    transfer((uint8_t)(address >> 8));
    transfer((uint8_t)address);
    for (uint32_t i = 0; i < length; i++)
        data[i] = transfer(0);
    gpio_write(GPIOE_BASE, CS, 1);
    delay_us(1);
}

uint8_t eeprom_status(void)
{
    gpio_write(GPIOE_BASE, CS, 0);
    delay_us(1);
    transfer(CMD_RDSR);
    uint8_t status = transfer(0);
    gpio_write(GPIOE_BASE, CS, 1);
    delay_us(1);
    return status;
}
