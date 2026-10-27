// USB MIDI test firmware: plays notes from the host on all 8 voices (any channel), answers the stock firmware's
// version request, and enters the bootloader's update mode on the stock update-mode SysEx, so flashing no longer
// needs the panel buttons. The panel lights while any note is held.
#include "clock.h"
#include "panel.h"
#include "regs.h"
#include "sh.h"
#include "update.h"
#include "usb.h"
#include "voice.h"

#define MASTER_VOLUME 0x6000u // about 37%, kept low on purpose

// PRO-800 SysEx: F0 00 20 32 00 01 24 00 <data> F7
static const uint8_t sysex_header[] = {0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x24, 0x00};
#define SYSEX_MAX 64

static volatile uint32_t ms_ticks;

void SysTick_Handler(void) { ms_ticks++; }

// Splits a SysEx message into USB MIDI event packets (cable 0) and sends them.
static void send_sysex(const uint8_t *message, int length)
{
    uint8_t packets[SYSEX_MAX / 3 * 4 + 4];
    int count = 0;
    for (int i = 0; i < length; i += 3) {
        int left = length - i;
        uint8_t *p = &packets[4 * count++];
        p[0] = left > 3 ? 0x4 : (uint8_t)(0x4 + left); // 4: start/continue; 5, 6, 7: ends with 1, 2, 3 bytes
        p[1] = message[i];
        p[2] = left > 1 ? message[i + 1] : 0;
        p[3] = left > 2 ? message[i + 2] : 0;
    }
    uint32_t start = ms_ticks;
    while (!usb_midi_write(packets, count) && ms_ticks - start < 100)
        ;
}

static void enter_update_mode(void)
{
    voice_all_off();
    sh_set(CV_MASTER_VOL, 0);
    for (int i = 0; i < 4; i++)
        sh_refresh();
    usb_disconnect();
    delay_us(100000); // let the host see the unplug before the bootloader re-enumerates
    update_mode_enter();
}

static void handle_sysex(const uint8_t *message, int length)
{
    int header = (int)sizeof sysex_header;
    if (length < header + 1 || message[length - 1] != 0xF7)
        return;
    for (int i = 0; i < header; i++)
        if (message[i] != sysex_header[i])
            return;
    const uint8_t *data = message + header;
    int data_length = length - header - 1;

    if (data_length == 2 && data[0] == 0x03 && data[1] == 0x30)
        enter_update_mode();
    if (data_length == 2 && data[0] == 0x08 && data[1] == 0x00) {
        uint8_t reply[] = {0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x24, 0x00, 0x09, 0x00,
                           FW_VERSION_MAJOR, FW_VERSION_MINOR, FW_VERSION_PATCH, 0xF7};
        send_sysex(reply, sizeof reply);
    }
}

static void handle_midi(void)
{
    static uint8_t sysex[SYSEX_MAX];
    static int sysex_length;
    uint8_t p[4];

    while (usb_midi_read(p)) {
        uint8_t cin = p[0] & 0x0F;
        uint8_t status = p[1] & 0xF0;
        switch (cin) {
        case 0x9:
            if (p[3])
                voice_note_on(p[2]);
            else
                voice_note_off(p[2]);
            break;
        case 0x8:
            voice_note_off(p[2]);
            break;
        case 0xB:
            if (status == 0xB0 && (p[2] == 120 || p[2] == 123)) // all sound off, all notes off
                voice_all_off();
            break;
        case 0x4: case 0x5: case 0x6: case 0x7: {
            int n = cin == 0x4 ? 3 : cin - 0x4;
            if (p[1] == 0xF0)
                sysex_length = 0;
            for (int i = 1; i <= n; i++)
                if (sysex_length < SYSEX_MAX)
                    sysex[sysex_length++] = p[i];
            if (cin != 0x4) {
                handle_sysex(sysex, sysex_length);
                sysex_length = 0;
            }
            break;
        }
        default:
            break;
        }
    }
}

int main(void)
{
    sh_safe();
    clock_init();
    sh_init();

    SYST_RVR = system_hz / 1000 - 1;
    SYST_CVR = 0;
    SYST_CSR = 7; // processor clock, interrupt, enable

    sh_set(CV_MASTER_VOL, 0);
    voice_init();
    for (int i = 0; i < 20; i++) // charge every S&H before the master volume comes up
        sh_refresh();
    sh_set(CV_MASTER_VOL, MASTER_VOLUME);

    panel_gpio_init();
    int have_panel = panel_init() != 0;
    uint8_t all_on[PANEL_RAM_BYTES], all_off[PANEL_RAM_BYTES] = {0};
    for (int i = 0; i < PANEL_RAM_BYTES; i++)
        all_on[i] = 0xFF;
    if (have_panel)
        panel_write(all_off);

    usb_init();

    int shown = 0;
    uint32_t last = ms_ticks;
    for (;;) {
        IWDG_KR = IWDG_KR_REFRESH;
        handle_midi();

        uint32_t now = ms_ticks;
        voice_update(now - last);
        last = now;

        int lit = voice_any_gate();
        if (have_panel && lit != shown) {
            panel_write(lit ? all_on : all_off);
            shown = lit;
        }

        sh_refresh();
    }
}
