// GPIO helpers: pins are bit masks within one port.
#ifndef GPIO_H
#define GPIO_H

#include "regs.h"

static inline void gpio_set_mode(uint32_t port, uint32_t pins, uint32_t mode)
{
    uint32_t moder = GPIO_MODER(port);
    for (int pin = 0; pin < 16; pin++)
        if (pins & 1u << pin)
            moder = (moder & ~(3u << 2 * pin)) | mode << 2 * pin;
    GPIO_MODER(port) = moder;
}

// Push-pull outputs. Set the level with GPIO_BSRR first, so the pin never drives the wrong one.
static inline void gpio_output(uint32_t port, uint32_t pins)
{
    GPIO_OTYPER(port) &= ~pins;
    gpio_set_mode(port, pins, 1);
}

static inline void gpio_alternate(uint32_t port, uint32_t pins, uint32_t af)
{
    for (int pin = 0; pin < 16; pin++)
        if (pins & 1u << pin) {
            GPIO_AFR(port, pin / 8) = (GPIO_AFR(port, pin / 8) & ~(0xFu << 4 * (pin % 8))) | af << 4 * (pin % 8);
            GPIO_OSPEEDR(port) = (GPIO_OSPEEDR(port) & ~(3u << 2 * pin)) | 2u << 2 * pin; // fast
        }
    GPIO_OTYPER(port) &= ~pins;
    gpio_set_mode(port, pins, 2);
}

static inline void gpio_analog(uint32_t port, uint32_t pins) { gpio_set_mode(port, pins, 3); }

static inline void gpio_write(uint32_t port, uint32_t pins, int high)
{
    GPIO_BSRR(port) = high ? pins : pins << 16;
}

#endif
