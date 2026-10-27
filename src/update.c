// The erase runs from RAM: code fetches from the bank being erased would stall and then read erased flash.
// Sector 4 holds the vector table and all of this firmware (it's under 64 kB); sector 5 isn't touched.
#include "update.h"
#include "regs.h"

#define APP_SECTOR 4

// Only register accesses in here: nothing in flash may be called once the erase starts.
__attribute__((section(".ramfunc"), noinline, noreturn))
static void erase_and_reset(void)
{
    if (FLASH_CR & FLASH_CR_LOCK) {
        FLASH_KEYR = FLASH_KEY1;
        FLASH_KEYR = FLASH_KEY2;
    }
    while (FLASH_SR & FLASH_SR_BSY)
        ;
    FLASH_SR = FLASH_SR_ERRORS;
    FLASH_CR = FLASH_CR_PSIZE_X32 | FLASH_CR_SER | FLASH_CR_SNB(APP_SECTOR);
    FLASH_CR |= FLASH_CR_STRT;
    while (FLASH_SR & FLASH_SR_BSY)
        IWDG_KR = IWDG_KR_REFRESH; // a 64 kB erase takes about a second
    FLASH_CR = FLASH_CR_LOCK;

    __asm volatile("dsb");
    SCB_AIRCR = SCB_AIRCR_SYSRESETREQ;
    for (;;)
        ;
}

void update_mode_enter(void)
{
    __asm volatile("cpsid i");
    // called through a pointer, since the RAM copy is out of range of a direct branch from flash
    void (*volatile erase)(void) = erase_and_reset;
    erase();
    for (;;)
        ;
}
