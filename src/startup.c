// Vector table and reset handler. The bootloader jumps here with its own clocks, interrupts and
// peripherals still set up, so Reset_Handler puts the chip back into a known state before main().
#include "regs.h"

extern uint32_t _estack, _sidata, _sdata, _edata, _sbss, _ebss;
extern void Reset_Handler(void);
extern void SysTick_Handler(void);
extern int main(void);

static void Default_Handler(void)
{
    for (;;)
        IWDG_KR = IWDG_KR_REFRESH;
}

// core exceptions plus the 97 STM32F446 interrupts; everything but SysTick on the default handler for now
__attribute__((section(".isr_vector"), used))
static void (*const vectors[16 + 97])(void) = {
    [0] = (void (*)(void))&_estack,
    [1] = Reset_Handler,
    [2 ... 14] = Default_Handler,
    [15] = SysTick_Handler,
    [16 ... 16 + 96] = Default_Handler,
};

static void reset_peripherals(void)
{
    RCC_AHB1RSTR = RCC_AHB1_RESET_MASK;
    RCC_AHB2RSTR = RCC_AHB2_RESET_MASK;
    RCC_APB1RSTR = RCC_APB1_RESET_MASK;
    RCC_APB2RSTR = RCC_APB2_RESET_MASK;
    RCC_AHB1RSTR = 0;
    RCC_AHB2RSTR = 0;
    RCC_APB1RSTR = 0;
    RCC_APB2RSTR = 0;
}

// Runs from the 16 MHz internal oscillator; the PLL comes later.
static void clock_to_hsi(void)
{
    RCC_CR |= RCC_CR_HSION;
    while (!(RCC_CR & RCC_CR_HSIRDY))
        ;
    RCC_CFGR = 0; // SYSCLK = HSI, no prescalers
    while (RCC_CFGR & RCC_CFGR_SWS)
        ;
    RCC_CR &= ~RCC_CR_PLLON;
    while (RCC_CR & RCC_CR_PLLRDY)
        ;
    RCC_CIR = 0x00BF0000u; // clear and disable clock interrupts
}

void Reset_Handler(void)
{
    __asm volatile("cpsid i");

    SCB_VTOR = (uint32_t)vectors;
    SYST_CSR = 0;
    for (int i = 0; i < 8; i++) {
        NVIC_ICER(i) = 0xFFFFFFFFu;
        NVIC_ICPR(i) = 0xFFFFFFFFu;
    }

    reset_peripherals();
    clock_to_hsi();

    SCB_CPACR |= 0xFu << 20; // FPU on (CP10, CP11)
    __asm volatile("dsb\n isb");

    uint32_t *src = &_sidata, *dst = &_sdata;
    while (dst < &_edata)
        *dst++ = *src++;
    for (dst = &_sbss; dst < &_ebss;)
        *dst++ = 0;

    __asm volatile("cpsie i");
    main();
    for (;;)
        IWDG_KR = IWDG_KR_REFRESH;
}
