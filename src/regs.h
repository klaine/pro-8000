// The few STM32F446 registers we use (RM0390), so the build needs no vendor headers.
#ifndef REGS_H
#define REGS_H

#include <stdint.h>

#define REG(addr) (*(volatile uint32_t *)(addr))

// RCC
#define RCC_BASE        0x40023800u
#define RCC_CR          REG(RCC_BASE + 0x00)
#define RCC_CFGR        REG(RCC_BASE + 0x08)
#define RCC_CIR         REG(RCC_BASE + 0x0C)
#define RCC_AHB1RSTR    REG(RCC_BASE + 0x10)
#define RCC_AHB2RSTR    REG(RCC_BASE + 0x14)
#define RCC_APB1RSTR    REG(RCC_BASE + 0x20)
#define RCC_APB2RSTR    REG(RCC_BASE + 0x24)
#define RCC_AHB1ENR     REG(RCC_BASE + 0x30)
#define RCC_APB1ENR     REG(RCC_BASE + 0x40)

#define RCC_CR_HSION    (1u << 0)
#define RCC_CR_HSIRDY   (1u << 1)
#define RCC_CR_PLLON    (1u << 24)
#define RCC_CR_PLLRDY   (1u << 25)
#define RCC_CFGR_SWS    (3u << 2)

// peripherals reset on entry, so nothing the bootloader left running stays active
#define RCC_AHB1_RESET_MASK 0x006000FFu // GPIOA-H, DMA1, DMA2
#define RCC_AHB2_RESET_MASK 0x00000080u // USB OTG FS
#define RCC_APB1_RESET_MASK 0x3FFFC9FFu // TIM2-7/12-14, WWDG, SPI2/3, SPDIFRX, USART2/3, UART4/5, I2C1-3, FMPI2C, CAN, CEC, PWR, DAC
#define RCC_APB2_RESET_MASK 0x00C77933u // TIM1/8/9-11, USART1/6, ADC, SDIO, SPI1/4, SYSCFG, SAI1/2

#define RCC_AHB1ENR_GPIOA (1u << 0)
#define RCC_AHB1ENR_GPIOB (1u << 1)
#define RCC_AHB1ENR_GPIOC (1u << 2)
#define RCC_AHB1ENR_GPIOD (1u << 3)
#define RCC_AHB1ENR_GPIOE (1u << 4)
#define RCC_APB1ENR_DAC   (1u << 29)

// GPIO
#define GPIOA_BASE      0x40020000u
#define GPIOB_BASE      0x40020400u
#define GPIOC_BASE      0x40020800u
#define GPIOD_BASE      0x40020C00u
#define GPIOE_BASE      0x40021000u
#define GPIO_MODER(p)   REG((p) + 0x00)
#define GPIO_OTYPER(p)  REG((p) + 0x04)
#define GPIO_OSPEEDR(p) REG((p) + 0x08)
#define GPIO_PUPDR(p)   REG((p) + 0x0C)
#define GPIO_IDR(p)     REG((p) + 0x10)
#define GPIO_BSRR(p)    REG((p) + 0x18)

// on-chip 12-bit DAC: channel 1 on PA4, channel 2 on PA5
#define DAC_BASE        0x40007400u
#define DAC_CR          REG(DAC_BASE + 0x00)
#define DAC_DHR12R1     REG(DAC_BASE + 0x08)
#define DAC_DHR12R2     REG(DAC_BASE + 0x14)
#define DAC_CR_EN1      (1u << 0)
#define DAC_CR_EN2      (1u << 16)

// independent watchdog: once the bootloader starts it, it can't be stopped, only refreshed
#define IWDG_KR         REG(0x40003000u)
#define IWDG_KR_REFRESH 0xAAAAu

// Cortex-M4 core
#define SCB_VTOR        REG(0xE000ED08u)
#define SCB_CPACR       REG(0xE000ED88u)
#define SYST_CSR        REG(0xE000E010u)
#define SYST_RVR        REG(0xE000E014u)
#define SYST_CVR        REG(0xE000E018u)
#define NVIC_ICER(n)    REG(0xE000E180u + 4u * (n))
#define NVIC_ICPR(n)    REG(0xE000E280u + 4u * (n))
#define DEMCR           REG(0xE000EDFCu)
#define DEMCR_TRCENA    (1u << 24)
#define DWT_CTRL        REG(0xE0001000u)
#define DWT_CYCCNT      REG(0xE0001004u)

#define HSI_HZ 16000000u

#endif
