// The PRO-800's hardware for the firmware on a computer, so tests can use the panel and read the display. The real
// src/main.c, synth.c, lfo.c, seq.c, store.c, display.c and controls.c run unchanged (with their debounce, key scan
// and pot hysteresis) against this file; tools/test_panel.py builds it and drives it through stdin and stdout.
//
// Each main-loop pass is exactly one millisecond of firmware time: the firmware runs in a fiber that comes back here
// when it refreshes the watchdog, at the top of a pass. Nothing else moves the clock once the loop runs, so a run is
// repeatable to the millisecond. A power cycle is a new process on the same EEPROM file.
//
// Commands, one per line, each answered with one line:
//   ms <n>                  run n passes
//   pot <index> <0-4095>    a knob's voltage          sw <index> <0|1>   a switch's pin level
//   btn <index> <0|1>       a button held or let go   enc <steps> <ms>   that many encoder transitions (signed), one per ms
//   midi <hex> / din <hex>  bytes into USB / DIN      out                what the firmware sent to USB, as hex
//   disp                    <ms> and the display RAM  hist               display changes since last asked: <ms>:<ram> ...
//   cv                      every CV                  gates              every gate
//   save                    write the EEPROM file     quit
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <ucontext.h>
#endif

#include "controls.c" // the real thing, whose pin and key tables the simulated inputs need
#include "clock.h"
#include "dac.h"
#include "din.h"
#include "eeprom.h"
#include "sh.h"
#include "tune.h"
#include "tuning.h"
#include "update.h"
#include "usb.h"

#undef main
extern int firmware_main(void);
extern void SysTick_Handler(void);

#define CYCLES_PER_MS 168000u
uint32_t system_hz = 168000000u;

// --- time and registers ---

static uint32_t cycles, tick_cycles;
static int in_loop; // the main loop has started: passes alone move the clock

static void advance_cycles(uint32_t n)
{
    cycles += n;
    while (cycles - tick_cycles >= CYCLES_PER_MS) {
        tick_cycles += CYCLES_PER_MS;
        SysTick_Handler();
    }
}

static void yield_to_host(void);

#define REG_SLOTS 1024
static struct { uint32_t address, value; } reg_table[REG_SLOTS];

static volatile uint32_t *reg_slot(uint32_t address)
{
    for (uint32_t i = (address >> 2) % REG_SLOTS;; i = (i + 1) % REG_SLOTS) {
        if (reg_table[i].address == address)
            return &reg_table[i].value;
        if (!reg_table[i].address) {
            reg_table[i].address = address;
            return &reg_table[i].value;
        }
    }
}

static uint16_t sim_pots[POT_COUNT];
static uint8_t sim_switches[SWITCH_COUNT];
static uint8_t sim_buttons[BUTTON_COUNT];
static int encoder_position, encoder_left, encoder_every, encoder_wait;

static uint32_t read_gpio(uint32_t port)
{
    uint32_t level = 0;
    for (int i = 0; i < SWITCH_COUNT; i++)
        if (switch_pins[i].port == port && sim_switches[i])
            level |= 1u << switch_pins[i].pin;
    if (port == GPIOE_BASE) { // the encoder: clockwise runs AB 11, 01, 00, 10
        static const uint8_t gray[4] = {3, 1, 0, 2};
        uint8_t state = gray[encoder_position & 3];
        level |= (uint32_t)(state >> 1) << ENC_A_PIN | (uint32_t)(state & 1) << ENC_B_PIN;
    }
    return level;
}

volatile uint32_t *sim_reg(uint32_t address)
{
    volatile uint32_t *slot = reg_slot(address);
    if (address == 0x40003000u) { // IWDG_KR: the top of a pass
        in_loop = 1;
        yield_to_host();
    } else if (address == 0xE0001004u) // DWT_CYCCNT
        *slot = cycles++;
    else if (address == 0x40012000u) // ADC1_SR: always done
        *slot = ADC_SR_EOC;
    else if (address == 0x4001204Cu) { // ADC1_DR: the channel asked for, behind the mux address last set
        uint32_t channel = *reg_slot(0x40012034u), mux = *reg_slot(GPIOD_BASE + 0x18u) & 7;
        *slot = channel == 13 ? sim_pots[POT_CUTOFF] : channel == 2 ? sim_pots[POT_NOISE]
              : sim_pots[(channel - 10) * 8 + mux];
        if (!in_loop)
            advance_cycles(20000); // the boot's wait for a first reading of every knob
    } else if (address == GPIOA_BASE + 0x10u || address == GPIOB_BASE + 0x10u || address == GPIOC_BASE + 0x10u
               || address == GPIOD_BASE + 0x10u || address == GPIOE_BASE + 0x10u)
        *slot = read_gpio(address - 0x10u);
    return slot;
}

int clock_init(void) { return 1; }
void delay_us(uint32_t us) { advance_cycles(us * 168u); }
void delay_cycles(uint32_t n) { advance_cycles(n); }

// --- the panel ---

#define HISTORY 8192
static uint8_t panel_ram[PANEL_RAM_BYTES];
static struct { uint32_t ms; uint8_t ram[PANEL_RAM_BYTES]; } history[HISTORY];
static int history_count;
static uint32_t now_ms_host;

void panel_gpio_init(void) {}
uint8_t panel_init(void) { return 0x70; }

void panel_write_range(const uint8_t ram[PANEL_RAM_BYTES], int first, int count)
{
    for (int i = first; i < first + count; i++)
        panel_ram[i] = ram[i];
    if (history_count < HISTORY) {
        history[history_count].ms = cycles / CYCLES_PER_MS;
        memcpy(history[history_count].ram, panel_ram, PANEL_RAM_BYTES);
        history_count++;
    }
}

void panel_write(const uint8_t ram[PANEL_RAM_BYTES]) { panel_write_range(ram, 0, PANEL_RAM_BYTES); }

int panel_read_keys(uint8_t keys_now[PANEL_KEY_BYTES])
{
    memset(keys_now, 0, PANEL_KEY_BYTES);
    for (int b = 0; b < BUTTON_COUNT; b++)
        if (sim_buttons[b])
            keys_now[button_keys[b].byte] |= (uint8_t)(1u << button_keys[b].bit);
    return 1;
}

int panel_key_flag(void)
{
    for (int b = 0; b < BUTTON_COUNT; b++)
        if (sim_buttons[b])
            return 1;
    return 0;
}

// --- the EEPROM: a file ---

static uint8_t eeprom[EEPROM_SIZE];
static const char *eeprom_path;

void eeprom_init(void) {}
void eeprom_read(uint32_t a, uint8_t *data, uint32_t n) { memcpy(data, eeprom + a, n); }
uint8_t eeprom_status(void) { return 0; }
int eeprom_write(uint32_t a, const uint8_t *data, uint32_t n)
{
    memcpy(eeprom + a, data, n);
    return 1;
}

static void eeprom_save(void)
{
    FILE *f = fopen(eeprom_path, "wb");
    if (f) {
        fwrite(eeprom, 1, EEPROM_SIZE, f);
        fclose(f);
    }
}

// --- MIDI ---

#define RING 8192
static uint8_t usb_in[RING][4], din_in[RING][4];
static int usb_in_head, usb_in_tail, din_in_head, din_in_tail;
static void (*usb_realtime)(uint8_t, uint16_t, uint32_t);
static void (*din_realtime)(uint8_t, uint16_t, uint32_t);
static uint8_t out_bytes[1u << 20];
static int out_count;

void usb_init(void) {}
void usb_midi_realtime(void (*handler)(uint8_t, uint16_t, uint32_t)) { usb_realtime = handler; }
void usb_disconnect(void) {}
void usb_midi_flush(void) { usb_in_tail = usb_in_head; }
int usb_midi_read(uint8_t packet[4])
{
    if (usb_in_tail == usb_in_head)
        return 0;
    memcpy(packet, usb_in[usb_in_tail], 4);
    usb_in_tail = (usb_in_tail + 1) % RING;
    return 1;
}
int usb_midi_write(const uint8_t *packets, int count)
{
    static const uint8_t lengths[16] = {0, 0, 2, 3, 3, 1, 2, 3, 3, 3, 3, 3, 2, 2, 3, 1};
    for (int i = 0; i < count; i++)
        for (int k = 0; k < lengths[packets[4 * i] & 15] && out_count < (int)sizeof out_bytes; k++)
            out_bytes[out_count++] = packets[4 * i + 1 + k];
    return 1;
}
void din_init(void (*handler)(uint8_t, uint16_t, uint32_t)) { din_realtime = handler; }
void din_midi_flush(void) { din_in_tail = din_in_head; }
int din_midi_read(uint8_t packet[4])
{
    if (din_in_tail == din_in_head)
        return 0;
    memcpy(packet, din_in[din_in_tail], 4);
    din_in_tail = (din_in_tail + 1) % RING;
    return 1;
}

static void push_packet(int din, uint8_t cin, uint8_t a, uint8_t b, uint8_t c)
{
    uint8_t *packet = din ? din_in[din_in_head] : usb_in[usb_in_head];
    packet[0] = cin;
    packet[1] = a;
    packet[2] = b;
    packet[3] = c;
    if (din)
        din_in_head = (din_in_head + 1) % RING;
    else
        usb_in_head = (usb_in_head + 1) % RING;
}

// Whole messages only, as USB MIDI packets
static void send_midi(int din, const uint8_t *m, int n)
{
    for (int i = 0; i < n;) {
        uint8_t status = m[i];
        if (status >= 0xF8) {
            void (*handler)(uint8_t, uint16_t, uint32_t) = din ? din_realtime : usb_realtime;
            if (handler)
                handler(status, 0, cycles);
            i++;
        } else if (status == 0xF0) {
            int end = i;
            while (end < n && m[end] != 0xF7)
                end++;
            int k = i;
            for (; end - k >= 3; k += 3)
                push_packet(din, 4, m[k], m[k + 1], m[k + 2]);
            int left = end - k + 1;
            push_packet(din, left == 1 ? 5 : left == 2 ? 6 : 7, m[k], left > 1 ? m[k + 1] : 0, left > 2 ? m[k + 2] : 0);
            i = end + 1;
        } else {
            int high = status >> 4, length = high == 0xC || high == 0xD ? 2 : 3;
            push_packet(din, (uint8_t)high, status, m[i + 1], length == 3 ? m[i + 2] : 0);
            i += length;
        }
    }
}

// --- CVs, gates and the rest of the voice board ---

static uint16_t cv_values[CV_COUNT];
static uint8_t gate_values[GATE_COUNT];

void sh_safe(void) {}
void sh_init(void) {}
void sh_set(cv_t cv, uint16_t value) { cv_values[cv] = value; }
void sh_gate(gate_t gate, int on) { gate_values[gate] = (uint8_t)(on != 0); }
void sh_set_timing(uint32_t settle, uint32_t sample) { (void)settle; (void)sample; }
void sh_timing(uint32_t *settle, uint32_t *sample) { *settle = *sample = 0; }
void sh_refresh(void) {}
void sh_refresh_pitch(unsigned voices) { (void)voices; }
void sh_refresh_cutoff(unsigned voices) { (void)voices; }
void sh_refresh_vca(unsigned skip) { (void)skip; }
void sh_refresh_global(void) {}
void sh_hold(void) {}
void sh_refresh_dac_setup(void) {}
void sh_dac_setup_resend(int on) { (void)on; }
void sh_dac_register(uint8_t command, uint16_t value) { (void)command; (void)value; }

const dac_config_t dac_default = {DAC_BUS_BITBANG, 0, 1, 1, 0, 0};
int dac_configure(const dac_config_t *config) { (void)config; return 1; }
const dac_config_t *dac_config(void) { return &dac_default; }

void tune_init(void) {}
tune_result_t tune_measure(uint32_t max_periods, uint32_t timeout_ms, int rising)
{
    (void)max_periods;
    (void)timeout_ms;
    (void)rising;
    tune_result_t r = {0, 0};
    return r;
}

// The tables: nominal, with no measuring. `--untuned` starts without them, as after a new EEPROM layout.
static int tuned = 1;
uint16_t tuning_point_cv(int point) { return (uint16_t)(0x2000 + 0x1000 * point); }
int tuning_load(void) { return tuned; }
int tuning_valid(void) { return tuned; }
int tuning_run(void)
{
    tuned = 1;
    advance_cycles(25000u * CYCLES_PER_MS); // about 25 s, in which the display stays as it is
    return TUNING_ROWS;
}
uint32_t tuning_centihertz(int row, int point) { (void)row; (void)point; return 0; }
// 256 per semitone around note 64, so a cutoff or pitch pushed 96 semitones either way still reads (notes -64..191)
uint16_t tuning_note_cv(int row, float note)
{
    (void)row;
    float cv = 32768.0f + (note - 64.0f) * 256.0f;
    return (uint16_t)(cv < 0 ? 0 : cv > 65535.0f ? 65535 : cv);
}

void update_mode_enter(void)
{
    printf("update mode\n");
    fflush(stdout);
    exit(0);
}

// --- the fiber the firmware runs in ---

#ifdef _WIN32
static void *host_fiber, *firmware_fiber;
static void yield_to_host(void) { SwitchToFiber(host_fiber); }
static void resume_firmware(void) { SwitchToFiber(firmware_fiber); }
static void CALLBACK firmware_entry(void *unused)
{
    (void)unused;
    firmware_main();
}
static void start_firmware(void)
{
    host_fiber = ConvertThreadToFiber(0);
    firmware_fiber = CreateFiber(1u << 21, firmware_entry, 0);
    resume_firmware();
}
#else
static ucontext_t host_context, firmware_context;
static char firmware_stack[1u << 21];
static void yield_to_host(void) { swapcontext(&firmware_context, &host_context); }
static void resume_firmware(void) { swapcontext(&host_context, &firmware_context); }
static void firmware_entry(void) { firmware_main(); }
static void start_firmware(void)
{
    getcontext(&firmware_context);
    firmware_context.uc_stack.ss_sp = firmware_stack;
    firmware_context.uc_stack.ss_size = sizeof firmware_stack;
    firmware_context.uc_link = 0;
    makecontext(&firmware_context, firmware_entry, 0);
    resume_firmware();
}
#endif

static void run_pass(void)
{
    if (encoder_left) {
        if (encoder_wait > 0)
            encoder_wait--;
        else {
            encoder_position += encoder_left > 0 ? 1 : -1;
            encoder_left += encoder_left > 0 ? -1 : 1;
            encoder_wait = encoder_every - 1;
        }
    }
    advance_cycles(CYCLES_PER_MS - cycles % CYCLES_PER_MS);
    resume_firmware();
}

static void print_ram(const uint8_t *ram)
{
    for (int i = 0; i < PANEL_RAM_BYTES; i++)
        printf("%02x", ram[i]);
}

static int parse_hex(const char *text, uint8_t *out, int max)
{
    int n = 0;
    unsigned value;
    while (n < max && sscanf(text, "%2x", &value) == 1) {
        out[n++] = (uint8_t)value;
        text += 2;
    }
    return n;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: pro800sim <eeprom file> [--untuned]\n");
        return 2;
    }
    eeprom_path = argv[1];
    FILE *f = fopen(eeprom_path, "rb");
    if (f) {
        if (fread(eeprom, 1, EEPROM_SIZE, f) != EEPROM_SIZE)
            memset(eeprom, 0, EEPROM_SIZE);
        fclose(f);
    }
    for (int i = 2; i < argc; i++)
        if (!strcmp(argv[i], "--untuned"))
            tuned = 0;

    for (int i = 0; i < POT_COUNT; i++)
        sim_pots[i] = 2048;
    for (int i = 0; i <= SWITCH_SYNC; i++)
        sim_switches[i] = 1; // the latching buttons down (off), keyboard tracking off

    start_firmware();
    printf("ready\n");
    fflush(stdout);

    static char line[1u << 20];
    static uint8_t bytes[1u << 18];
    while (fgets(line, sizeof line, stdin)) {
        int a, b;
        char text[8];
        if (sscanf(line, "ms %d", &a) == 1) {
            for (int i = 0; i < a; i++)
                run_pass();
            printf("ok\n");
        } else if (sscanf(line, "pot %d %d", &a, &b) == 2) {
            sim_pots[a] = (uint16_t)b;
            printf("ok\n");
        } else if (sscanf(line, "sw %d %d", &a, &b) == 2) {
            sim_switches[a] = (uint8_t)b;
            printf("ok\n");
        } else if (sscanf(line, "btn %d %d", &a, &b) == 2) {
            sim_buttons[a] = (uint8_t)b;
            printf("ok\n");
        } else if (sscanf(line, "enc %d %d", &a, &b) == 2) {
            encoder_left = a;
            encoder_every = b < 1 ? 1 : b;
            encoder_wait = 0;
            printf("ok\n");
        } else if (!strncmp(line, "midi ", 5) || !strncmp(line, "din ", 4)) {
            int din = line[0] == 'd';
            send_midi(din, bytes, parse_hex(line + (din ? 4 : 5), bytes, (int)sizeof bytes));
            printf("ok\n");
        } else if (!strncmp(line, "out", 3)) {
            for (int i = 0; i < out_count; i++)
                printf("%02x", out_bytes[i]);
            out_count = 0;
            printf("\n");
        } else if (!strncmp(line, "disp", 4)) {
            printf("%u ", cycles / CYCLES_PER_MS);
            print_ram(panel_ram);
            printf("\n");
        } else if (!strncmp(line, "hist", 4)) {
            for (int i = 0; i < history_count; i++) {
                printf("%u:", history[i].ms);
                print_ram(history[i].ram);
                printf(" ");
            }
            history_count = 0;
            printf("\n");
        } else if (sscanf(line, "%7s", text) == 1 && !strcmp(text, "cv")) {
            for (int i = 0; i < CV_COUNT; i++)
                printf("%u ", cv_values[i]);
            printf("\n");
        } else if (!strncmp(line, "gates", 5)) {
            for (int i = 0; i < GATE_COUNT; i++)
                printf("%u ", gate_values[i]);
            printf("\n");
        } else if (!strncmp(line, "save", 4)) {
            eeprom_save();
            printf("ok\n");
        } else if (!strncmp(line, "quit", 4)) {
            eeprom_save();
            printf("ok\n");
            fflush(stdout);
            break;
        } else
            printf("? %s", line);
        fflush(stdout);
    }
    (void)now_ms_host;
    return 0;
}
