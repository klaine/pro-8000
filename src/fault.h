// What the last crash left behind. src/startup.c's handler writes it and main.c shows and reports it (SysEx 49). It
// lives in RAM the startup code leaves alone, so it outlives the reset. The bootloader runs in between and may use that
// RAM too (not known yet: `pro800 fault-test` on the synth tells). Then the bad magic or check word says so and the record
// is lost, and with it the streak: each crash counts as the first, so a firmware that crashes at boot would restart for
// ever instead of stopping at the third, still with buttons 0 and 7 at power-up as the way out. If that turns out to
// happen, the streak needs a place the bootloader can't touch, such as an RTC backup register.
#ifndef FAULT_H
#define FAULT_H

#include <stdint.h>

#define FAULT_MAGIC 0x43725368u // "CrSh"
#define FAULT_STREAK_LIMIT 3    // this many crashes without FAULT_CLEAN_MS of uptime between them: no more resets
#define FAULT_CLEAN_MS 10000u
#define FAULT_FRAME_BYTES 32u   // the stacked r0-r3, r12, lr, pc, xPSR; with the FPU's registers the frame is longer

typedef struct {
    uint32_t magic, check;
    uint32_t exception;      // its number (IPSR): 3 hard fault, 16 + n interrupt n
    uint32_t streak;         // crashes since the last FAULT_CLEAN_MS of uptime
    uint32_t pc, lr;         // from the stacked frame; 0 when the stack pointer didn't point at one
    uint32_t exc_return;     // the LR the exception came in with: bit 3 thread mode, bit 4 clear with FPU registers
    uint32_t cfsr, hfsr, mmfar, bfar; // the fault status and address registers
} fault_record_t;

extern volatile fault_record_t fault_record;

// A read of an address nothing answers at (a precise bus fault). Doesn't come back.
void fault_provoke(void) __attribute__((noreturn));

static inline uint32_t fault_checksum(const volatile fault_record_t *r)
{
    const volatile uint32_t *word = (const volatile uint32_t *)r;
    uint32_t sum = word[0];
    for (unsigned i = 2; i < sizeof(fault_record_t) / 4; i++) // all but the check word itself
        sum = (sum << 5 | sum >> 27) ^ word[i];
    return sum;
}

static inline int fault_valid(const volatile fault_record_t *r)
{
    return r->magic == FAULT_MAGIC && r->check == fault_checksum(r);
}

// At boot: what's in RAM after a power-up is noise.
static inline void fault_boot_check(volatile fault_record_t *r)
{
    if (fault_valid(r))
        return;
    volatile uint32_t *word = (volatile uint32_t *)r;
    for (unsigned i = 0; i < sizeof(fault_record_t) / 4; i++)
        word[i] = 0;
}

// Uptime has shown the last crash wasn't a boot crash. The rest of the record stays for the probe.
static inline void fault_clear_streak(volatile fault_record_t *r)
{
    if (!fault_valid(r) || !r->streak)
        return;
    r->streak = 0;
    r->check = fault_checksum(r);
}

// The handler's logic: fills the record for a crash whose stacked frame is at `frame`, read only if it's aligned and
// within RAM with room for the whole frame (after a stack overflow it isn't). Returns 1 to reset, 0 to stay put. Only
// registers and this record: no call, no .data, no .bss.
static inline int fault_store(volatile fault_record_t *r, uintptr_t frame, uintptr_t ram_start, uintptr_t ram_end,
                              uint32_t exception, uint32_t exc_return, uint32_t cfsr, uint32_t hfsr, uint32_t mmfar,
                              uint32_t bfar)
{
    uint32_t streak = (fault_valid(r) ? r->streak : 0) + 1, pc = 0, lr = 0;
    if (!(frame & 3) && frame >= ram_start && frame <= ram_end - FAULT_FRAME_BYTES) {
        const uint32_t *stacked = (const uint32_t *)frame;
        lr = stacked[5];
        pc = stacked[6];
    }
    r->magic = FAULT_MAGIC;
    r->exception = exception;
    r->streak = streak;
    r->pc = pc;
    r->lr = lr;
    r->exc_return = exc_return;
    r->cfsr = cfsr;
    r->hfsr = hfsr;
    r->mmfar = mmfar;
    r->bfar = bfar;
    r->check = fault_checksum(r);
    return streak < FAULT_STREAK_LIMIT;
}

#endif
