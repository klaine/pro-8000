// Force-included in the simulator's build (tools/test_panel.py): the registers the firmware uses, on the host. The
// firmware's own regs.h stays as it is; only what REG() expands to changes.
#ifndef SIM_REGS_H
#define SIM_REGS_H

#include "regs.h"

volatile uint32_t *sim_reg(uint32_t address);

#undef REG
#define REG(addr) (*sim_reg(addr))

#endif
