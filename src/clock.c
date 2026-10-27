// 8 MHz HSE / M 8 = 1 MHz, × N 336 = 336 MHz VCO; / P 2 = 168 MHz SYSCLK, / Q 7 = 48 MHz for USB.
// AHB 168 MHz, APB1 42 MHz (max 45), APB2 84 MHz (max 90). 168 MHz runs in voltage scale 1 (the reset value)
// without over-drive.
#include "clock.h"
#include "regs.h"

#define PLL_M 8u
#define PLL_N 336u
#define PLL_P_DIV2 0u
#define PLL_SRC_HSE (1u << 22)
#define PLL_Q 7u
#define PLL_R 2u

#define FLASH_LATENCY_5WS 5u
#define FLASH_PRFTEN (1u << 8)
#define FLASH_ICEN   (1u << 9)
#define FLASH_DCEN   (1u << 10)

#define CFGR_PPRE1_DIV4 (5u << 10)
#define CFGR_PPRE2_DIV2 (4u << 13)

#define HSE_STARTUP_LOOPS 1000000

uint32_t system_hz = HSI_HZ;

void delay_us(uint32_t us)
{
    uint32_t start = DWT_CYCCNT;
    uint32_t cycles = us * (system_hz / 1000000u);
    while (DWT_CYCCNT - start < cycles)
        ;
}

int clock_init(void)
{
    DEMCR |= DEMCR_TRCENA;
    DWT_CYCCNT = 0;
    DWT_CTRL |= 1; // cycle counter on

    RCC_CR |= RCC_CR_HSEON;
    for (int i = 0; !(RCC_CR & RCC_CR_HSERDY); i++)
        if (i == HSE_STARTUP_LOOPS) {
            RCC_CR &= ~RCC_CR_HSEON;
            return 0; // stay on HSI
        }

    RCC_PLLCFGR = PLL_M | PLL_N << 6 | PLL_P_DIV2 << 16 | PLL_SRC_HSE | PLL_Q << 24 | PLL_R << 28;
    RCC_CR |= RCC_CR_PLLON;
    while (!(RCC_CR & RCC_CR_PLLRDY))
        ;

    FLASH_ACR = FLASH_LATENCY_5WS | FLASH_PRFTEN | FLASH_ICEN | FLASH_DCEN;
    while ((FLASH_ACR & 0xFu) != FLASH_LATENCY_5WS)
        ;

    RCC_CFGR = CFGR_PPRE1_DIV4 | CFGR_PPRE2_DIV2 | RCC_CFGR_SW_PLL;
    while ((RCC_CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL)
        ;

    system_hz = PLL_N * (HSE_HZ / PLL_M) / 2;
    return 1;
}
