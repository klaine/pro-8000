// Bit-banged SPI mode 0, about 250 kHz: WP = PE1, SCK = PE2, HOLD = PE3, CS = PE4, MISO (SO) = PE5,
// MOSI (SI) = PE6. WP stays low: it only guards the status register, which is never written.
#include "eeprom.h"
#include "clock.h"
#include "gpio.h"

#define WP   (1u << 1)
#define SCK  (1u << 2)
#define HOLD (1u << 3)
#define CS   (1u << 4)
#define MISO_PIN 5
#define MOSI (1u << 6)

#define CMD_READ  0x03
#define CMD_RDSR  0x05
#define CMD_WREN  0x06
#define CMD_WRITE 0x02
#define STATUS_WIP 0x01
#define WRITE_TIMEOUT_US 20000

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

static void command(uint8_t cmd)
{
    gpio_write(GPIOE_BASE, CS, 0);
    delay_us(1);
    transfer(cmd);
    gpio_write(GPIOE_BASE, CS, 1);
    delay_us(1);
}

static int wait_ready(void)
{
    for (int us = 0; us < WRITE_TIMEOUT_US; us += 50) {
        if (!(eeprom_status() & STATUS_WIP))
            return 1;
        delay_us(50);
    }
    return 0;
}

int eeprom_write(uint32_t address, const uint8_t *data, uint32_t length)
{
    while (length) {
        uint32_t room = EEPROM_PAGE - address % EEPROM_PAGE, n = length < room ? length : room;
        if (!wait_ready())
            return 0;
        command(CMD_WREN);
        gpio_write(GPIOE_BASE, CS, 0);
        delay_us(1);
        transfer(CMD_WRITE);
        transfer((uint8_t)(address >> 16));
        transfer((uint8_t)(address >> 8));
        transfer((uint8_t)address);
        for (uint32_t i = 0; i < n; i++)
            transfer(data[i]);
        gpio_write(GPIOE_BASE, CS, 1); // the write cycle starts here
        delay_us(1);
        address += n;
        data += n;
        length -= n;
    }
    return wait_ready();
}
