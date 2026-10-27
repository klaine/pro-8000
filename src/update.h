// Entering the bootloader's update mode from the running firmware.
#ifndef UPDATE_H
#define UPDATE_H

// Erases this firmware's vector table (flash sector 4 at 0x08010000) and resets. With no valid application the
// bootloader stays in update mode, as after the stock firmware's update-mode SysEx (which also survives a
// power cycle). Doesn't return.
void update_mode_enter(void) __attribute__((noreturn));

#endif
