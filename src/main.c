// The PRO-800 firmware: the synth engine (synth.c) plays MIDI notes from USB (any channel) with the sound set on
// the front panel. Turning a pot shows its value (0-99) for a moment; Tune runs autotune; a button's LED lights
// while it's held. It answers the stock firmware's version request and enters the bootloader's update mode on the
// stock update-mode SysEx, so flashing doesn't need the panel buttons.
//
// Probe commands (our own, after the PRO-800 header), for exploring the hardware from the host:
//   74 <0|1>              direct mode off/on: on stops the voice engine, so the host owns every CV and gate
//   70 <cv> <v2> <v1> <v0> set a CV to v2 << 14 | v1 << 7 | v0
//   71 <gate> <0|1>        set a gate
//   72 <p1> <p0> <t1> <t0> [<rising>]
//                          time up to p1 << 7 | p0 periods of TUNE_CPR (falling edges, or rising), timeout
//                          t1 << 7 | t0 ms; replies 73 <ok> <periods, 2 × 7 bits> <cycles, 5 × 7 bits>
//                          <system_hz / 1000, 3 × 7 bits>
//   75                     run autotune (about 25 s) and save it; replies 76 <rows measured, of 24>
//   77 <row>               tuning table row (osc A 1-8, osc B 1-8, filter 1-8); replies
//                          77 <row> <points> <centihertz, 4 × 7 bits, per point; 0 = not measured>
//   78 <voice> <osc> <note> set voice's oscillator (0 A, 1 B) to a MIDI note through the tables
//   7A <0|1>               panel report mode off/on: on sends every control change, and leaves the display to 7B
//                          60 <pot> <v1> <v0>   61 <switch> <0|1>   62 <key byte> <v1> <v0>   63 <64 ± transitions>
//                          64 <button> <0|1>
//   7B <address> <v1> <v0> write a byte of the HT16K33 display RAM (address 0-15)
//   7C                     panel state; replies 7C <26 pots, 2 × 7 bits each> <24 switches, 0/1 each>
// Each other command is acknowledged with 7F <command>.
#include "clock.h"
#include "controls.h"
#include "display.h"
#include "panel.h"
#include "regs.h"
#include "sh.h"
#include "tune.h"
#include "tuning.h"
#include "update.h"
#include "usb.h"
#include "synth.h"


// PRO-800 SysEx: F0 00 20 32 00 01 24 00 <data> F7
static const uint8_t sysex_header[] = {0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x24, 0x00};
#define SYSEX_MAX 112

static volatile uint32_t ms_ticks;
static int direct_mode;
static int report_mode;
static uint8_t display_ram[PANEL_RAM_BYTES];

void SysTick_Handler(void) { ms_ticks++; }

// Splits a SysEx message into USB MIDI event packets (cable 0) and sends them.
static void send_sysex(const uint8_t *message, int length)
{
    uint8_t packets[(SYSEX_MAX + 2) / 3 * 4];
    int count = 0;
    for (int i = 0; i < length; i += 3) {
        int left = length - i;
        uint8_t *p = &packets[4 * count++];
        p[0] = left > 3 ? 0x4 : (uint8_t)(0x4 + left); // 4: start/continue; 5, 6, 7: ends with 1, 2, 3 bytes
        p[1] = message[i];
        p[2] = left > 1 ? message[i + 1] : 0;
        p[3] = left > 2 ? message[i + 2] : 0;
    }
    // the endpoint takes 16 packets (64 bytes) at a time
    for (int sent = 0; sent < count;) {
        int chunk = count - sent > 16 ? 16 : count - sent;
        uint32_t start = ms_ticks;
        while (!usb_midi_write(&packets[4 * sent], chunk))
            if (ms_ticks - start > 100)
                return;
        sent += chunk;
    }
}

static void report_event(control_kind_t kind, int index, int32_t value)
{
    uint8_t m[] = {0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x24, 0x00, (uint8_t)(0x60 + kind), (uint8_t)index, 0, 0, 0xF7};
    int n = 10;
    switch (kind) {
    case CONTROL_POT:
    case CONTROL_KEYS:
        m[n++] = (uint8_t)((value >> 7) & 0x7F);
        m[n++] = (uint8_t)(value & 0x7F);
        break;
    case CONTROL_SWITCH:
    case CONTROL_BUTTON:
        m[n++] = (uint8_t)value;
        break;
    case CONTROL_ENCODER:
        m[n++] = (uint8_t)(64 + value);
        break;
    }
    m[n++] = 0xF7;
    send_sysex(m, n);
}

static void show_version(void)
{
    char text[] = {(char)('0' + FW_VERSION_MAJOR), '.', (char)('0' + FW_VERSION_MINOR), '.',
                   (char)('0' + FW_VERSION_PATCH), 0};
    display_text(text);
    display_update();
}

static uint32_t display_until; // ms_ticks when a pot value on the display gives way; 0 = nothing shown
static int tune_requested;

static void control_event(control_kind_t kind, int index, int32_t value)
{
    static const led_t button_led[BUTTON_COUNT] = {
        [BUTTON_PRESET] = LED_PRESET, [BUTTON_PERF] = LED_PERF, [BUTTON_SETTINGS] = LED_SETTINGS,
        [BUTTON_TUNE] = LED_TUNE, [BUTTON_SYNC_SOURCE] = LED_SYNC_SOURCE, [BUTTON_SYNC_CLOCK] = LED_SYNC_CLOCK,
        [BUTTON_REC] = LED_REC, [BUTTON_SEQ1] = LED_SEQ1, [BUTTON_SEQ2] = LED_SEQ2, [BUTTON_ARP_UP] = LED_ARP_UP,
        [BUTTON_ARP_ASSIGN] = LED_ARP_ASSIGN,
    };

    if (report_mode) {
        report_event(kind, index, value);
        return;
    }
    if (kind == CONTROL_POT) {
        display_number((int)(value * 99 / 4095));
        display_until = ms_ticks + 1500;
    }
    if (kind == CONTROL_BUTTON && index > BUTTON_9) {
        display_led(button_led[index], (int)value);
        if (index == BUTTON_TUNE && value)
            tune_requested = 1;
    }
}

static void run_tuning(void)
{
    synth_all_off();
    display_text("tUnE");
    display_update();
    tuning_run();
    synth_init();
    display_clear();
}

static void enter_update_mode(void)
{
    synth_all_off();
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

    uint8_t reply[SYSEX_MAX] = {0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x24, 0x00};
    int n = header;

    if (data_length == 2 && data[0] == 0x03 && data[1] == 0x30)
        enter_update_mode();
    if (data_length == 2 && data[0] == 0x74) {
        direct_mode = data[1] != 0;
        synth_all_off();
        if (!direct_mode)
            synth_init();
        reply[n++] = 0x7F;
        reply[n++] = 0x74;
    }
    if (data_length == 5 && data[0] == 0x70 && data[1] < CV_COUNT) {
        sh_set((cv_t)data[1], (uint16_t)(data[2] << 14 | data[3] << 7 | data[4]));
        reply[n++] = 0x7F;
        reply[n++] = 0x70;
    }
    if (data_length == 3 && data[0] == 0x71 && data[1] < GATE_COUNT) {
        sh_gate((gate_t)data[1], data[2] != 0);
        reply[n++] = 0x7F;
        reply[n++] = 0x71;
    }
    if ((data_length == 5 || data_length == 6) && data[0] == 0x72) {
        tune_result_t r = tune_measure((uint32_t)(data[1] << 7 | data[2]), (uint32_t)(data[3] << 7 | data[4]),
                                       data_length == 6 && data[5]);
        uint32_t khz = system_hz / 1000;
        reply[n++] = 0x73;
        reply[n++] = r.cycles != 0;
        reply[n++] = (r.periods >> 7) & 0x7F;
        reply[n++] = r.periods & 0x7F;
        for (int shift = 28; shift >= 0; shift -= 7)
            reply[n++] = (r.cycles >> shift) & 0x7F;
        for (int shift = 14; shift >= 0; shift -= 7)
            reply[n++] = (khz >> shift) & 0x7F;
    }
    if (data_length == 2 && data[0] == 0x7A) {
        report_mode = data[1] != 0;
        for (int i = 0; i < PANEL_RAM_BYTES; i++)
            display_ram[i] = 0;
        panel_write(display_ram);
        if (!report_mode)
            display_redraw();
        reply[n++] = 0x7F;
        reply[n++] = 0x7A;
    }
    if (data_length == 4 && data[0] == 0x7B && data[1] < PANEL_RAM_BYTES) {
        display_ram[data[1]] = (uint8_t)(data[2] << 7 | data[3]);
        panel_write(display_ram);
        reply[n++] = 0x7F;
        reply[n++] = 0x7B;
    }
    if (data_length == 1 && data[0] == 0x7C) {
        reply[n++] = 0x7C;
        for (int i = 0; i < POT_COUNT; i++) {
            reply[n++] = (uint8_t)(controls_pot(i) >> 7);
            reply[n++] = controls_pot(i) & 0x7F;
        }
        for (int i = 0; i < SWITCH_COUNT; i++)
            reply[n++] = (uint8_t)controls_switch(i);
    }
    if (data_length == 1 && data[0] == 0x75) {
        synth_all_off();
        int rows = tuning_run();
        synth_init();
        if (direct_mode)
            sh_set(CV_MASTER_VOL, 0); // otherwise the engine sets it from M VOL
        reply[n++] = 0x76;
        reply[n++] = (uint8_t)rows;
    }
    if (data_length == 2 && data[0] == 0x77 && data[1] < TUNING_ROWS) {
        reply[n++] = 0x77;
        reply[n++] = data[1];
        reply[n++] = TUNING_POINTS;
        for (int p = 0; p < TUNING_POINTS; p++) {
            uint32_t centihertz = tuning_centihertz(data[1], p);
            for (int shift = 21; shift >= 0; shift -= 7)
                reply[n++] = (centihertz >> shift) & 0x7F;
        }
    }
    if (data_length == 4 && data[0] == 0x78 && data[1] < VOICE_COUNT && data[2] < 2) {
        int row = (data[2] ? TUNING_ROW_OSC_B : TUNING_ROW_OSC_A) + data[1];
        sh_set((cv_t)((data[2] ? CV_OSC_B1 : CV_OSC_A1) + data[1]), tuning_note_cv(row, data[3]));
        reply[n++] = 0x7F;
        reply[n++] = 0x78;
    }
    if (n > header) {
        reply[n++] = 0xF7;
        send_sysex(reply, n);
    }
    if (data_length == 2 && data[0] == 0x08 && data[1] == 0x00) {
        uint8_t version[] = {0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x24, 0x00, 0x09, 0x00,
                             FW_VERSION_MAJOR, FW_VERSION_MINOR, FW_VERSION_PATCH, 0xF7};
        send_sysex(version, sizeof version);
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
            if (direct_mode)
                break;
            if (p[3])
                synth_note_on(p[2], p[3]);
            else
                synth_note_off(p[2]);
            break;
        case 0x8:
            synth_note_off(p[2]);
            break;
        case 0xB:
            if (status == 0xB0 && (p[2] == 120 || p[2] == 123)) // all sound off, all notes off
                synth_all_off();
            if (status == 0xB0 && p[2] == 1)
                synth_mod_wheel(p[3]);
            break;
        case 0xE:
            synth_pitch_bend(p[2] | p[3] << 7);
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
    SCB_SHPR3 = (SCB_SHPR3 & 0x00FFFFFFu) | 0x40u << 24; // SysTick below the tuning edge interrupt
    tune_init();

    sh_set(CV_MASTER_VOL, 0); // the engine sets it from the M VOL pot once the pots have been read
    tuning_load();
    synth_init();
    for (int i = 0; i < 20; i++) // charge every S&H
        sh_refresh();

    panel_gpio_init();
    panel_init();
    display_led(LED_POWER, 1);

    // The bootloader erases the tables with every firmware update, so the first boot after one tunes (about
    // 30 s, silent), showing "tUnE" meanwhile.
    if (!tuning_valid()) {
        display_text("tUnE");
        display_update();
        tuning_run();
        synth_init();
    }
    show_version();
    display_until = ms_ticks + 1500;

    controls_init();
    usb_init();

    uint32_t last = DWT_CYCCNT;
    for (;;) {
        IWDG_KR = IWDG_KR_REFRESH;
        handle_midi();

        uint32_t cycles = DWT_CYCCNT;
        float dt = (float)(cycles - last) / (float)system_hz;
        last = cycles;

        uint32_t now = ms_ticks;
        controls_poll(now, control_event);
        if (tune_requested) {
            tune_requested = 0;
            run_tuning();
            last = DWT_CYCCNT;
        }
        if (!direct_mode)
            synth_update(dt);

        if (display_until && (int32_t)(now - display_until) > 0) {
            display_clear();
            display_until = 0;
        }
        if (!report_mode)
            display_update();

        sh_refresh();
    }
}
