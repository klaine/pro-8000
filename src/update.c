// The erase runs from RAM: code fetches from the bank being erased would stall and then read erased flash.
// Sector 4 holds the vector table and all of this firmware (it's under 64 kB); sector 5 isn't touched.
#include "update.h"
#include "regs.h"

#define FLASH_KEYR REG(0x40023C04u)
#define FLASH_SR   REG(0x40023C0Cu)
#define FLASH_CR   REG(0x40023C10u)

#define FLASH_KEY1 0x45670123u
#define FLASH_KEY2 0xCDEF89ABu
#define FLASH_SR_ERRORS (0x1F0u) // WRPERR, PGAERR, PGPERR, PGSERR, RDERR
#define FLASH_SR_BSY    (1u << 16)
#define FLASH_CR_SER    (1u << 1)
#define FLASH_CR_SNB(n) ((uint32_t)(n) << 3)
#define FLASH_CR_PSIZE_X32 (2u << 8)
#define FLASH_CR_STRT   (1u << 16)
#define FLASH_CR_LOCK   (1u << 31)

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
