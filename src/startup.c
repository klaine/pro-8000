// Vector table and reset handler. The bootloader jumps here with its own clocks, interrupts and
// peripherals still set up, so Reset_Handler puts the chip back into a known state before main().
#include "fault.h"
#include "regs.h"
#include "sh.h"

extern uint32_t _sram, _estack, _sidata, _sdata, _edata, _sbss, _ebss;
extern void Reset_Handler(void);
extern void SysTick_Handler(void);
extern void OTG_FS_IRQHandler(void);
extern void EXTI9_5_IRQHandler(void);
extern void UART4_IRQHandler(void);
extern int main(void);

// In .noinit: the bss loop stops short of it. fault.h says what can happen to it across a reset.
__attribute__((section(".noinit"))) volatile fault_record_t fault_record;

// Every exception and every interrupt nothing asked for comes here. Only registers until SP is safe: a stack overflow
// must not fault again. SP goes back to the top of RAM, except when it's already within 256 bytes of it: the frame is
// up there then, and fault_report()'s own stack would land on it before it's read. (fault_report() uses far less than
// that, so from further down it can't reach the frame.)
__attribute__((naked)) static void fault_entry(void)
{
    __asm volatile(
        "cpsid i\n"
        "mrs r0, msp\n" // the frame: nothing here runs on the process stack
        "mrs r1, ipsr\n"
        "mov r2, lr\n"
        "movw r3, #:lower16:_estack\n"
        "movt r3, #:upper16:_estack\n"
        "sub.w r12, r3, r0\n"
        "cmp.w r12, #256\n"
        "it ls\n"
        "movls r3, r0\n"
        "mov sp, r3\n"
        "b.w fault_report\n");
}

// Three crashes in a row with no 10 s of uptime between them stay here feeding the watchdog, as every crash used to, so
// a crash at boot can't reboot for ever and a power cycle holding buttons 0 and 7 reaches the bootloader.
__attribute__((used, noreturn))
static void fault_report(uint32_t frame, uint32_t ipsr, uint32_t exc_return)
{
    int reset = fault_store(&fault_record, frame, (uint32_t)&_sram, (uint32_t)&_estack, ipsr & 0x1FFu, exc_return,
                            SCB_CFSR, SCB_HFSR, SCB_MMFAR, SCB_BFAR);
    __asm volatile("dsb"); // the record is in RAM before the reset
    if (reset)
        SCB_AIRCR = SCB_AIRCR_SYSRESETREQ;
    __asm volatile("dsb");
    for (;;)
        IWDG_KR = IWDG_KR_REFRESH;
}

// core exceptions plus the 97 STM32F446 interrupts; SysTick, EXTI9_5 (IRQ 23, tuning), UART4 (IRQ 52, MIDI in) and
// USB (IRQ 67) have handlers
__attribute__((section(".isr_vector"), used))
static void (*const vectors[16 + 97])(void) = {
    [0] = (void (*)(void))&_estack,
    [1] = Reset_Handler,
    [2 ... 14] = fault_entry,
    [15] = SysTick_Handler,
    [16 ... 16 + 22] = fault_entry,
    [16 + 23] = EXTI9_5_IRQHandler,
    [16 + 24 ... 16 + 51] = fault_entry,
    [16 + 52] = UART4_IRQHandler,
    [16 + 53 ... 16 + 66] = fault_entry,
    [16 + 67] = OTG_FS_IRQHandler,
    [16 + 68 ... 16 + 96] = fault_entry,
};

// Reserved in RM0390's memory map: past the SRAM, below the peripherals. Bus faults are not enabled, so this escalates
// to a hard fault with BFAR holding the address. Should it ever answer, the undefined instruction faults for certain.
void fault_provoke(void)
{
    (void)*(volatile uint32_t *)0x30000000u;
    __asm volatile("udf #0");
    for (;;)
        IWDG_KR = IWDG_KR_REFRESH;
}

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

// No .data or .bss yet, and the FPU is off until its line below: everything before that is registers and constants.
// Off the bootloader's fast clock first, since the reset mask holds PWR, which must not reset under it. The GPIO reset
// lets the S&H enables float, so sh_safe() drives them high straight after it, not after the bss loop.
__attribute__((used, noreturn))
static void reset_body(void)
{
    SCB_VTOR = (uint32_t)vectors;
    SYST_CSR = 0;
    for (int i = 0; i < 8; i++) {
        NVIC_ICER(i) = 0xFFFFFFFFu;
        NVIC_ICPR(i) = 0xFFFFFFFFu;
    }

    clock_to_hsi();
    reset_peripherals();
    sh_safe();

    SCB_CPACR |= 0xFu << 20; // FPU on (CP10, CP11)
    __asm volatile("dsb\n isb");
    SCB_FPCCR |= SCB_FPCCR_ASPEN | SCB_FPCCR_LSPEN; // the reset values, which the bootloader may have changed

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

// The bootloader's stack and CONTROL are not ours: its SP may be anything, and a set FPCA or SPSEL would change how the
// first exception stacks. So this sets them before any code that uses the stack.
__attribute__((naked)) void Reset_Handler(void)
{
    __asm volatile(
        "cpsid i\n"
        "movw r0, #:lower16:_estack\n"
        "movt r0, #:upper16:_estack\n"
        "msr msp, r0\n"
        "movs r0, #0\n"
        "msr control, r0\n"
        "isb\n"
        "b.w reset_body\n");
}
