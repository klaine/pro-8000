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
//   6C <bus> <mode> <h1> <h0> <l1> <l0> <speed> <sync us> <settle> <sample>
//                          probe the CV path (until reboot; dac.h): the DAC80501 bus (bit-bang / SPI3, mode, high
//                          and low cycles or SPI divider, GPIO speed, SYNC wait) and the S&H waits in tenths of a us
//                          (0 = default); acknowledged with 7F 6C <1 if taken>
//   6D                     the probe settings; replies 6D <the same 10 bytes as 6C>
//   75                     run autotune (about 25 s) and save it; replies 76 <rows measured, of 24>
//   77 <row>               tuning table row (osc A 1-8, osc B 1-8, filter 1-8); replies
//                          77 <row> <points> <centihertz, 4 × 7 bits, per point; 0 = not measured>
//   78 <voice> <osc> <note> set voice's oscillator (0 A, 1 B) to a MIDI note through the tables
//   7A <0|1>               panel report mode off/on: on sends every control change, and leaves the display to 7B
//                          60 <pot> <v1> <v0>   61 <switch> <0|1>   62 <key byte> <v1> <v0>   63 <64 ± transitions>
//                          64 <button> <0|1>
//   7B <address> <v1> <v0> write a byte of the HT16K33 display RAM (address 0-15)
//   7C                     panel state; replies 7C <26 pots, 2 × 7 bits each> <24 switches, 0/1 each>
//   7D <setting> <value>   set a setting (host_setting_t: 0 LFO 1 sync, 1 LFO 1 retrigger, 2 layer mode, 3 split
//                          note, 4 keyboard tracking, 5 LFO 2 sync, 6 LFO 2 retrigger, 7 edit layer, 8 selected LFO,
//                          9-12 LFO shapes (layer 1 LFO 1, LFO 2, layer 2 LFO 1, LFO 2), 13-14 LFO 2 routing per layer,
//                          15-16 cycling envelopes per layer, 17-24 synced cycling per envelope: layer 1 amp,
//                          filter, Env 3, Env 4, then layer 2; 0 free, else clock division + 1), 25 bend range
//                          (1-12 semitones), 26 unison detune (0-50 cents), 27-28 voice modes per layer (mode 0 poly
//                          1 mono 2 legato | priority 0 last 1 low 2 high << 2), 29 drift (0-99), 30 scale (0 off,
//                          synth.h SCALE_*), 31 root (0-11); read-only after: env select, preset mode, patch, morph
//   44 <t1> <t0>           set the morph target: patch + 1 (0 = none); acknowledged
//   54                     every setting; replies 54 <count> <value, 2 × 7 bits, per setting> (the settable ones,
//                          then env select, preset mode, current patch)
//   7E <layer>             choose the layer the per-layer knobs edit (0 or 1)
//   53 <layer>             the layer's parameters; replies 53 <layer> <count> <value 0-16383, 2 × 7 bits, each>
//   52 <layer> <param> <v1> <v0>  set a parameter (0-16383; its knob goes into pickup); acknowledged
//   59 <n1> <n0> <name>    rename patch n (up to 16 characters); acknowledged
//   4F <n1> <n0>           clear patch n (it becomes an empty slot); acknowledged
//   4E <layer> <lfo> <count> <p2> <p1> <p0>  set an LFO's step pattern (count 1-16, bits p2 << 14 | p1 << 7 | p0,
//                          bit n = step n on); acknowledged
//   4D <layer> <lfo>       its step pattern; replies 4D <layer> <lfo> <count> <p2> <p1> <p0>
//   48                     the sequence's settings; replies 48 <length> <lanes> <division> <bpm, 2 × 7 bits> <flags>
//                          <swing> <smooth> <max steps for these lanes> <arp>
//   47 <length> <lanes> <division> <bpm, 2 × 7 bits> <flags> <swing> <smooth> <arp>  set them (clamped to fit)
//   46 <first> <n ≤ 8>     steps; replies 46 <first> <n> then per step <note offset + 64> <velocity, 0 = the key's>
//                          <gate % (100 = tie)> <on> <extra: probability | ratchets - 1 << 3> <lane A-D values, 64 = 0>
//   45 <first> <n ≤ 8> <9 bytes per step, as 46 replies>  set steps; acknowledged. The sequence is saved with the
//                          patch (REC, 5D).
//   4C                     the buttons as the engine uses them; replies 4C <switches 0-15, 0/1 each> (switch_t order;
//                          a loaded patch's state until the button is toggled)
//   4B <switch> <0|1>      set a button's state (0-15), held until the button is toggled; acknowledged
//   50 <f>                 factory patch f (0-99); replies 50 <f> <1 if it's there> <name>
//   51 <f> <n1> <n0> [<1>] copy factory patch f into slot n, converted; only into an empty slot unless the last
//                          byte is 1; replies 51 <0 done, 1 slot not empty, 2 no such factory patch, 3 write error>
//   5A <a2> <a1> <a0> <n> <2n nibbles>  write n (1-48) bytes to the patch EEPROM; acknowledged when written
//   5B                     import: convert every stock patch in place, then mark the EEPROM as ours; replies
//                          5B <converted, 2 × 7 bits> <errors>
//   5C <n1> <n0>           load patch n; 5D <n1> <n0> save the current sound to patch n (both acknowledged)
//   5F                     load the init sound (as PERF does; acknowledged)
//   56 <layer> <source> <dest> <d1> <d0>  set a route's depth (d1 << 7 | d0) - 99, 0 removes it; acknowledged
//   58                     timing; replies 58 <engine, refresh, pass, longest pass, panel poll, display: µs,
//                          passes in the last second: 3 × 7 bits each>
//   57 <layer>             the layer's routes; replies 57 <layer> then per route <source> <dest> <d1> <d0>
//   5E <n1> <n0>           patch info; replies 5E <n1> <n0> <kind: 0 empty, 1 ours, 2 stock, 3 corrupt> <name>
//   79 <a2> <a1> <a0> <n>  read n (1-48) bytes of the patch EEPROM at a2 << 14 | a1 << 7 | a0; replies
//                          79 <a2> <a1> <a0> <n> <status register, 2 nibbles> <each byte as 2 nibbles, high first>
// Each other command is acknowledged with 7F <command>.
#include "clock.h"
#include "controls.h"
#include "display.h"
#include "eeprom.h"
#include "store.h"
#include "lfo.h"
#include "panel.h"
#include "regs.h"
#include "dac.h"
#include "din.h"
#include "sh.h"
#include "tune.h"
#include "tuning.h"
#include "update.h"
#include "usb.h"
#include "seq.h"
#include "synth.h"


// PRO-800 SysEx: F0 00 20 32 00 01 24 00 <data> F7
static const uint8_t sysex_header[] = {0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x24, 0x00};
#define SYSEX_MAX 120

static volatile uint32_t ms_ticks;
static int direct_mode;
static uint32_t cycles_engine, cycles_refresh, cycles_pass, cycles_pass_max, cycles_controls, cycles_display; // probe (58)
static uint32_t passes, passes_per_second, passes_second;
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

// Patches: PRESET toggles preset mode (a patch loaded) and manual mode (the panel live). In preset mode two keypad
// digits pick a patch in the current bank and VALUE steps through all 400; REC, then the slot, then REC saves;
// PERF loads the init sound.
static int current_patch, preset_mode, saving, typed_digits, typed_value, encoder_patch_steps;
static char loaded_name[PATCH_NAME + 1]; // travels with the sound when it's saved to an empty slot
static uint32_t typed_at;

static void patch_label(int n, char *text) // " A.07"
{
    static const char banks[] = "Ab";
    text[0] = ' ';
    text[1] = banks[n / 100];
    text[2] = '.';
    text[3] = (char)('0' + n % 100 / 10);
    text[4] = (char)('0' + n % 10);
    text[5] = 0;
}

static void show_patch(int n)
{
    char text[6];
    patch_label(n, text);
    display_text(text);
}

// The morph target's knob values (target = patch + 1, 0 = none)
static void load_morph(int target)
{
    patch_t t;
    synth_set_morph(target, target && store_load_patch(target - 1, &t) ? &t : 0);
}

static int load_patch(int n)
{
    patch_t patch;
    if (!store_load_patch(n, &patch)) {
        display_text("nonE");
        display_until = ms_ticks + 1000;
        return 0;
    }
    seq_stop();
    synth_apply_patch(&patch);
    if (!store_load_sequence(n))
        seq_clear();
    load_morph(patch.morph);
    for (int i = 0; i <= PATCH_NAME; i++)
        loaded_name[i] = patch.name[i];
    current_patch = n;
    preset_mode = 1;
    display_led(LED_PRESET, 1);
    show_patch(n);
    store_set_last(n, 1);
    return 1;
}

// PERF loads a basic sound: oscillator A saw at concert pitch, filter open, gate-like amp envelope, nothing else.
static void init_patch(void)
{
    static const struct { uint8_t pot; uint16_t value; } values[] = {
        {POT_FREQ_A, 0x8000}, {POT_FREQ_B, 0x8000}, {POT_FINE, 0x8000}, {POT_MASTER_TUNE, 0x8000},
        {POT_A_LEVEL, 0xCCCC}, {POT_B_LEVEL, 0}, {POT_NOISE, 0}, {POT_A_PW, 0x8000}, {POT_B_PW, 0x8000},
        {POT_CUTOFF, 0xFFFF}, {POT_RESONANCE, 0}, {POT_FIL_ENV_AMT, 0x8000},
        {POT_FIL_ATT, 0}, {POT_FIL_DEC, 0x8000}, {POT_FIL_SUS, 0xFFFF}, {POT_FIL_REL, 0x4000},
        {POT_AMP_ATT, 0}, {POT_AMP_DEC, 0x8000}, {POT_AMP_SUS, 0xFFFF}, {POT_AMP_REL, 0x4000},
        {POT_GLIDE, 0}, {POT_LFO_FREQ, 0x8000}, {POT_LFO_AMOUNT, 0}, {POT_PMOD_OSC_B, 0}, {POT_PMOD_FIL_ENV, 0},
        {POT_MASTER_VOL, 0x8000},
    };
    static const char name[] = "Init";
    patch_t patch = {0};
    for (int l = 0; l < 2; l++)
        for (unsigned i = 0; i < sizeof values / sizeof values[0]; i++)
            patch.pots[l][values[i].pot] = values[i].value;
    patch.switches = 1u << SWITCH_A_SAW;
    for (int l = 0; l < 2; l++)
        patch.extra[l][PARAM_LFO2_RATE - POT_COUNT] = 0x8000;
    patch.keytrack = 2;
    patch.layer_mode = LAYER_OFF;
    patch.split = 60; // LFOs free-running triangles, LFO 2 with no routing
    seq_clear();
    synth_apply_patch(&patch);
    synth_set_morph(0, 0);
    for (int i = 0; i <= PATCH_NAME; i++)
        loaded_name[i] = i < (int)sizeof name ? name[i] : 0;
    preset_mode = 1; // the slot number stays; REC saves the init sound wherever you choose
    display_led(LED_PRESET, 1);
    display_text("InIt");
    display_until = ms_ticks + 1500;
}

static void manual_mode(void)
{
    loaded_name[0] = 0; // a sound made on the panel has no name yet
    synth_manual();
    preset_mode = 0;
    display_led(LED_PRESET, 0);
    display_text("PAnL");
    display_until = ms_ticks + 1000;
    store_set_last(current_patch, 0);
}

static int save_patch(int n)
{
    patch_t patch, old;
    synth_capture_patch(&patch);
    const char *name = store_load_patch(n, &old) && old.name[0] ? old.name : loaded_name; // the slot's, else ours
    for (int i = 0; i <= PATCH_NAME; i++)
        patch.name[i] = name[i];
    if (!store_save_patch(n, &patch) || !store_save_sequence(n))
        return 0;
    current_patch = n;
    preset_mode = 1;
    display_led(LED_PRESET, 1);
    store_set_last(n, 1);
    return 1;
}

// Settings menu: Settings opens and closes it, a number key picks a page, the VALUE knob changes the value. The
// LFO pages set the LFO that Arp Up-Dn selects ("SYn2" / "trI2" for LFO 2).
// Key 6 sets cycling for one envelope of the edit layer: pressing 6 again moves to the next envelope.
enum { PAGE_LFO_SYNC, PAGE_LFO_TRIGGER, PAGE_LAYERS, PAGE_SPLIT, PAGE_KEYTRACK, PAGE_CYCLE, PAGE_BEND, PAGE_DETUNE,
       PAGE_VOICE, PAGE_DRIFT, PAGE_COUNT }; // keys 1-9, then 0

static const char page_names[PAGE_COUNT][5] = {"SYnC", "trIG", "LAYr", "SPLt", "trAC", "", "bEnd", "dEtU", "UOIC",
                                                "drFt"};
static const char cycle_names[4][6] = {"CY.A", "CY.F", "CY.3", "CY.4"}; // amp, filter, Env 3, Env 4
static int cycle_env; // the envelope page 6 shows

static const char *page_name(int page)
{
    if (page == PAGE_CYCLE)
        return cycle_names[cycle_env];
    if (synth_lfo_select() && page == PAGE_LFO_SYNC)
        return "SYn2";
    if (synth_lfo_select() && page == PAGE_LFO_TRIGGER)
        return "trI2";
    return page_names[page];
}

static int menu_open, menu_page = -1, encoder_steps;
static uint32_t show_value_at; // ms_ticks to replace the page name with its value; 0 = not pending

static int setting_count(int page)
{
    static const int counts[PAGE_COUNT] = {LFO_SYNC_COUNT, LFO_TRIGGER_COUNT, LAYER_MODE_COUNT, 128, 3,
                                           2 + LFO_DIVISIONS, // cycle: OFF, FrEE, a clock division
                                           12, 51,            // bend 1-12 semitones, detune 0-50 cents
                                           3, 100};           // the edit layer's voice mode, drift 0-99
    return counts[page];
}

static int setting_get(int page)
{
    switch (page) {
    case PAGE_LFO_SYNC: return (int)lfo_sync[synth_lfo_select()];
    case PAGE_LFO_TRIGGER: return (int)lfo_trigger[synth_lfo_select()];
    case PAGE_LAYERS: return (int)synth_layer_mode();
    case PAGE_KEYTRACK: return synth_keytrack();
    case PAGE_BEND: return synth_bend_range() - 1;
    case PAGE_DETUNE: return synth_detune();
    case PAGE_VOICE: return synth_voice_mode(synth_edit_layer()) & 3;
    case PAGE_DRIFT: return synth_drift();
    case PAGE_CYCLE:
        if (!(synth_env_cycle(synth_edit_layer()) >> cycle_env & 1))
            return 0;
        return 1 + synth_env_sync(synth_edit_layer(), cycle_env);
    default: return synth_split_note();
    }
}

static void setting_set(int page, int value)
{
    switch (page) {
    case PAGE_LFO_SYNC: lfo_sync[synth_lfo_select()] = (lfo_sync_t)value; break;
    case PAGE_LFO_TRIGGER: lfo_trigger[synth_lfo_select()] = (lfo_trigger_t)value; break;
    case PAGE_LAYERS: synth_set_layer_mode((layer_mode_t)value); break;
    case PAGE_KEYTRACK: synth_set_keytrack(value); break;
    case PAGE_BEND: synth_set_bend_range(value + 1); break;
    case PAGE_DETUNE: synth_set_detune(value); break;
    case PAGE_VOICE: { // the mode; the note priority stays (set it on the web page)
        int layer = synth_edit_layer();
        synth_set_voice_mode(layer, (synth_voice_mode(layer) & ~3) | value);
        break;
    }
    case PAGE_DRIFT: synth_set_drift(value); break;
    case PAGE_CYCLE: {
        int bits = synth_env_cycle(synth_edit_layer());
        synth_set_env_cycle(synth_edit_layer(), value ? bits | 1 << cycle_env : bits & ~(1 << cycle_env));
        synth_set_env_sync(synth_edit_layer(), cycle_env, value >= 2 ? value - 1 : 0);
        break;
    }
    default: synth_set_split_note(value); break;
    }
}

static void show_setting(int page)
{
    static const char *const values[][LFO_TRIGGER_COUNT] = {
        {"FrEE", "CLoC"}, {"FrEE", "1St", "EACH", "PoLY"}, {"OFF", "SPLt", "StAC", "CHAn"}, {0}, {"OFF", "HALF", "FULL"},
    };
    int cycle = page == PAGE_CYCLE ? setting_get(page) : 0;
    if (page == PAGE_SPLIT)
        display_number(synth_split_note());
    else if (page == PAGE_BEND)
        display_number(synth_bend_range());
    else if (page == PAGE_DETUNE)
        display_number(synth_detune());
    else if (page == PAGE_DRIFT)
        display_number(synth_drift());
    else if (page == PAGE_VOICE) {
        static const char modes[3][5] = {"POLY", "nono", "LEG"};
        display_text(modes[setting_get(page)]);
    }
    else if (cycle >= 2)
        display_text(lfo_division_name(cycle - 2));
    else if (page == PAGE_CYCLE)
        display_text(cycle ? "FrEE" : "OFF");
    else
        display_text(values[page][setting_get(page)]);
}

static void show_edit_layer(void)
{
    display_led(LED_SEQ1, synth_edit_layer() == 0);
    display_led(LED_SEQ2, synth_edit_layer() == 1);
    display_led(LED_ARP_UP, synth_lfo_select());
}

// LFO shape: every toggle of the shape button steps to the next shape of the selected LFO (on the edit layer), and
// for a moment after it VALUE steps through them both ways.
static uint32_t shape_until; // ms_ticks until which VALUE picks the shape; 0 = not
static int shape_steps;

static void step_lfo_shape(int step)
{
    int layer = synth_edit_layer(), which = synth_lfo_select();
    int shape = (synth_lfo_shape(layer, which) + step + LFO_SHAPE_COUNT) % LFO_SHAPE_COUNT;
    synth_set_lfo_shape(layer, which, shape);
    display_text(lfo_shape_name((lfo_shape_t)shape));
    display_until = ms_ticks + 2000;
    shape_until = display_until;
    shape_steps = 0;
}

// MOD ASSIGN (the Arp Assign button): a number key picks the source, touching a knob or an LFO routing button
// picks the destination (the knobs are in pickup meanwhile, and a toggled button keeps its routing), VALUE sets
// the depth for the edit layer. Arp Assign again leaves.
static int assigning, assign_source, assign_dest = DEST_NONE, assign_steps;

static const char source_names[SRC_COUNT][5] = {
    "", "LFO1", "FEnV", "AEnV", "VEL", "nod", "PrES", "EnV3", "EnV4", "rAnd", "notE", "LFO2",
};
static const char dest_names[DEST_COUNT][6] = {
    "P.A", "P.b", "PIt", "CUt", "F.EA", "P.EA", "L.An", "L.Fr", "L.FL", "VCA",
    "A.LE", "b.LE", "noIS", "rES", "A.PU", "b.PU", "P.ob", "L.PU",
    "L.rt", "L2.An", "L2.rt", "L2.Fr", "L2.FL", "L2.PU",
};

static void show_route(void)
{
    display_number(synth_route(synth_edit_layer(), (mod_source_t)assign_source, (mod_dest_t)assign_dest));
}

static void assign_end(void)
{
    assigning = 0;
    display_led(LED_ARP_ASSIGN, 0);
    display_until = 0;
}

// Handles a control event while assigning. Returns 1 if it was used.
static int assign_event(control_kind_t kind, int index, int32_t value)
{
    if (kind == CONTROL_BUTTON && index <= BUTTON_9 && value) {
        int source = index == BUTTON_0 ? SRC_NOTE : index - BUTTON_0;
        if (source == SRC_LFO) // key 1: the selected LFO; pressed again, the other one
            source = assign_source == SRC_LFO ? SRC_LFO2 : assign_source == SRC_LFO2 ? SRC_LFO
                   : synth_lfo_select() ? SRC_LFO2 : SRC_LFO;
        assign_source = source;
        assign_dest = DEST_NONE;
        display_text(source_names[assign_source]);
        display_until = 0;
        return 1;
    }
    int dest = kind == CONTROL_POT ? synth_dest_for_pot(index)
             : kind == CONTROL_SWITCH ? synth_dest_for_switch(index) : DEST_NONE - 1;
    if (kind == CONTROL_SWITCH && dest != DEST_NONE)
        synth_hold_switch(index); // selecting, not switching
    if (dest != DEST_NONE - 1) {
        if (kind == CONTROL_SWITCH && dest == DEST_NONE)
            return 0; // other buttons work as usual
        if (!assign_source) {
            display_text("SrC");
            return 1;
        }
        if (dest == DEST_NONE) {
            display_text("no");
            return 1;
        }
        if (dest != assign_dest) {
            assign_dest = dest;
            assign_steps = 0;
            display_text(dest_names[dest]);
            show_value_at = ms_ticks + 700;
        }
        return 1;
    }
    if (kind == CONTROL_ENCODER) {
        if (!assign_source || assign_dest == DEST_NONE) {
            display_text(assign_source ? "dESt" : "SrC");
            return 1;
        }
        assign_steps += (int)value;
        if (assign_steps >= 2 || assign_steps <= -2) { // a step per half cycle: -99..99 takes a few turns
            int layer = synth_edit_layer();
            int depth = synth_route(layer, (mod_source_t)assign_source, (mod_dest_t)assign_dest) + assign_steps / 2;
            assign_steps = 0;
            if (!synth_set_route(layer, (mod_source_t)assign_source, (mod_dest_t)assign_dest, depth))
                display_text("FULL");
            else
                show_route();
            show_value_at = 0;
        }
        return 1;
    }
    return 0;
}

// What the display shows when nothing transient is on it.
static void idle_display(void)
{
    if (assigning && assign_source && assign_dest != DEST_NONE)
        show_route();
    else if (assigning)
        display_text(assign_source ? source_names[assign_source] : "SrC");
    else if (menu_open && menu_page >= 0)
        show_setting(menu_page);
    else if (menu_open)
        display_text("SEt");
    else if (saving || preset_mode)
        show_patch(current_patch);
    else
        display_clear();
}

// Which envelope (0 amp, 1 filter, 2 Env 3, 3 Env 4: the ENV_CYCLE_* bit order) and stage (0 attack, 1 decay,
// 2 sustain, 3 release) a knob edits; 0 if it isn't an envelope knob.
static int env_knob(int pot, int *env, int *stage)
{
    static const int8_t stages[POT_COUNT] = {
        [POT_AMP_ATT] = 1, [POT_AMP_DEC] = 2, [POT_AMP_SUS] = 3, [POT_AMP_REL] = 4,
        [POT_FIL_ATT] = 1, [POT_FIL_DEC] = 2, [POT_FIL_SUS] = 3, [POT_FIL_REL] = 4,
    };
    if (!stages[pot])
        return 0;
    *env = pot <= POT_AMP_ATT ? 0 : synth_env_select() + 1; // POT_AMP_REL..POT_AMP_ATT come first
    *stage = stages[pot] - 1;
    return 1;
}

// An envelope knob's time in seconds (attack, decay, release; hold and rest when cycling), or -1 for the others.
static float env_knob_time(int pot, float knob)
{
    int env, stage;
    if (!env_knob(pot, &env, &stage))
        return -1;
    return synth_env_time(stage, knob, (synth_env_cycle(synth_edit_layer()) >> env) & 1);
}

// 4 digits for a quantity: " 0.05", " 1.60", " 51.2", " 737"; from 1000 on in thousands with a point (" 1.24",
// " 16.7"), so a cutoff of 1.24 kHz shows as 1.24
static void format_quantity(float v, char *text)
{
    if (v >= 999.5f)
        v /= 1000.0f;
    int n = 0;
    text[n++] = ' ';
    if (v < 9.995f) {
        int c = (int)(v * 100.0f + 0.5f);
        text[n++] = (char)('0' + c / 100);
        text[n++] = '.';
        text[n++] = (char)('0' + c / 10 % 10);
        text[n++] = (char)('0' + c % 10);
    } else if (v < 99.95f) {
        int d = (int)(v * 10.0f + 0.5f);
        text[n++] = (char)('0' + d / 100);
        text[n++] = (char)('0' + d / 10 % 10);
        text[n++] = '.';
        text[n++] = (char)('0' + d % 10);
    } else {
        int i = (int)(v + 0.5f);
        text[n++] = (char)('0' + i / 100 % 10);
        text[n++] = (char)('0' + i / 10 % 10);
        text[n++] = (char)('0' + i % 10);
    }
    text[n] = 0;
}

// Signed, right-aligned (a 7-segment digit can't show '+'): one decimal under 10 ("-4.5", " 0.7"), else whole
// ("-36", "  72")
static void format_signed(float v, char *text)
{
    float a = v < 0 ? -v : v;
    int n = 0;
    int d = (int)(a * 10.0f + 0.5f);
    if (a < 9.95f && d % 10) { // a tenth to show (whole numbers, and what rounds to 0, take the plain path)
        text[n++] = ' ';
        text[n++] = v < 0 ? '-' : ' ';
        text[n++] = (char)('0' + d / 10);
        text[n++] = '.';
        text[n++] = (char)('0' + d % 10);
        text[n] = 0;
        return;
    }
    int i = (int)(a + 0.5f), k = 4;
    text[4] = 0;
    do {
        text[--k] = (char)('0' + i % 10);
        i /= 10;
    } while (i && k > 0);
    if (k > 0)
        text[--k] = v < 0 && (int)(a + 0.5f) ? '-' : ' ';
    while (k > 0)
        text[--k] = ' ';
}

// 4 digits: milliseconds as a whole number ("  15", " 150"), seconds with a point (" 1.25", " 12.5")
static void format_time(float s, char *text)
{
    int n = 0;
    if (s < 0.9995f) {
        int ms = (int)(s * 1000.0f + 0.5f);
        text[n++] = ' ';
        text[n++] = ms >= 100 ? (char)('0' + ms / 100) : ' ';
        text[n++] = ms >= 10 ? (char)('0' + ms / 10 % 10) : ' ';
        text[n++] = (char)('0' + ms % 10);
    } else if (s < 9.995f) {
        int c = (int)(s * 100.0f + 0.5f);
        text[n++] = ' ';
        text[n++] = (char)('0' + c / 100);
        text[n++] = '.';
        text[n++] = (char)('0' + c / 10 % 10);
        text[n++] = (char)('0' + c % 10);
    } else {
        int d = (int)(s * 10.0f + 0.5f);
        text[n++] = ' ';
        text[n++] = (char)('0' + d / 100);
        text[n++] = (char)('0' + d / 10 % 10);
        text[n++] = '.';
        text[n++] = (char)('0' + d % 10);
    }
    text[n] = 0;
}

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
    if (kind == CONTROL_BUTTON && index == BUTTON_ARP_ASSIGN && value && !menu_open && !saving) {
        if (assigning)
            assign_end();
        else {
            assigning = 1;
            assign_source = 0;
            assign_dest = DEST_NONE;
            synth_freeze_knobs();
            display_text("SrC");
            display_until = 0;
        }
        return;
    }
    if (assigning && kind == CONTROL_BUTTON && value && (index == BUTTON_PRESET || index == BUTTON_REC
                                                        || index == BUTTON_SETTINGS || index == BUTTON_PERF))
        assign_end(); // and the button does its usual job
    if (assigning && assign_event(kind, index, value))
        return;
    if (kind == CONTROL_SWITCH && index == SWITCH_LFO_SHAPE)
        step_lfo_shape(1);
    if (kind == CONTROL_ENCODER && shape_until && (int32_t)(ms_ticks - shape_until) < 0) {
        shape_steps += (int)value;
        if (shape_steps >= 4 || shape_steps <= -4)
            step_lfo_shape(shape_steps > 0 ? 1 : -1);
        return;
    }
    if (kind == CONTROL_BUTTON && index == BUTTON_ARP_UP && value) {
        synth_set_lfo_select(!synth_lfo_select());
        show_edit_layer();
        display_text(synth_lfo_select() ? "LFO2" : "LFO1");
        display_until = ms_ticks + 1000;
        if (menu_open && (menu_page == PAGE_LFO_SYNC || menu_page == PAGE_LFO_TRIGGER)) {
            display_until = 0;
            show_value_at = ms_ticks + 700; // then the page's value for this LFO
        }
    }
    if (kind == CONTROL_SWITCH && synth_lfo_select()
        && (index == SWITCH_LFO_FREQ || index == SWITCH_LFO_FILTER || index == SWITCH_LFO_PW)) {
        char text[] = "Fr.on";
        if (index == SWITCH_LFO_FILTER)
            text[1] = 'L';
        if (index == SWITCH_LFO_PW) {
            text[0] = 'P';
            text[1] = 'U';
        }
        if (!synth_lfo2_toggle(index))
            text[4] = 'F'; // "oF"
        display_text(text);
        display_until = ms_ticks + 1000;
    }
    if (kind == CONTROL_SWITCH && (index == SWITCH_KEYTRACK_HALF || index == SWITCH_KEYTRACK_FULL)) {
        static const char names[3][5] = {"FEnV", "EnV3", "EnV4"};
        display_text(names[controls_keytrack()]); // the envelope the filter ADSR knobs now edit
        display_until = ms_ticks + 1000;
    }
    if (kind == CONTROL_POT) {
        // a per-layer knob still in pickup shows the edit layer's value, with a dot
        int pending = synth_pot_pending(index);
        float knob = pending ? synth_pot_value(index) : (float)value / 4095.0f;
        char text[8];
        int env, stage;
        if (index == POT_LFO_FREQ && lfo_sync[synth_lfo_select()] == LFO_SYNC_CLOCK) {
            const char *name = lfo_division_name(lfo_division(knob));
            int n = 0;
            while (name[n] && n < 4) {
                text[n] = name[n];
                n++;
            }
            text[n] = 0;
        } else if (env_knob(index, &env, &stage) && synth_env_share(env, stage, knob) >= 0) {
            int share = (int)(synth_env_share(env, stage, knob) * 100.0f + 0.5f); // percent of the synced cycle
            text[0] = ' ';
            text[1] = share >= 100 ? '1' : ' ';
            text[2] = share >= 10 ? (char)('0' + share / 10 % 10) : ' ';
            text[3] = (char)('0' + share % 10);
            text[4] = 0;
        } else if (env_knob_time(index, knob) >= 0) {
            format_time(env_knob_time(index, knob), text);
        } else if (index == POT_FREQ_A || index == POT_FREQ_B) {
            format_signed((float)synth_knob_semitones(knob), text);
        } else if (index == POT_FINE || index == POT_MASTER_TUNE) {
            format_signed((float)synth_knob_cents(knob), text);
        } else if (index == POT_FIL_ENV_AMT) {
            format_signed(synth_knob_env_amount(knob), text); // semitones
        } else if (index == POT_PMOD_FIL_ENV) {
            format_quantity(synth_knob_pmod(knob), text); // semitones
        } else if (index == POT_CUTOFF) {
            format_quantity(synth_knob_cutoff_hz(knob), text); // Hz; with a point from 1 kHz on: kHz
        } else if (index == POT_LFO_FREQ) {
            format_quantity(lfo_rate_hz(knob), text); // Hz
        } else if (index == POT_GLIDE) {
            if (synth_knob_glide_s(knob) > 0)
                format_time(synth_knob_glide_s(knob), text);
            else
                text[0] = 'O', text[1] = 'F', text[2] = 'F', text[3] = 0;
        } else {
            int v = (int)(knob * 99.0f + 0.5f);
            text[0] = ' ';
            text[1] = ' ';
            text[2] = v >= 10 ? (char)('0' + v / 10) : ' ';
            text[3] = (char)('0' + v % 10);
            text[4] = 0;
        }
        if (pending) {
            int n = 0;
            while (text[n])
                n++;
            text[n] = '.';
            text[n + 1] = 0;
        }
        display_text(text);
        display_until = ms_ticks + 1500;
    }
    if (kind == CONTROL_BUTTON && (index == BUTTON_SEQ1 || index == BUTTON_SEQ2) && value) {
        synth_set_edit_layer(index == BUTTON_SEQ2);
        show_edit_layer();
        display_text(index == BUTTON_SEQ2 ? "L  2" : "L  1");
        display_until = ms_ticks + 1000;
    }
    if (kind == CONTROL_BUTTON && index > BUTTON_9) {
        if (index != BUTTON_SETTINGS && index != BUTTON_SEQ1 && index != BUTTON_SEQ2 && index != BUTTON_PRESET
            && index != BUTTON_REC && index != BUTTON_ARP_ASSIGN && index != BUTTON_ARP_UP)
            display_led(button_led[index], (int)value);
        if (index == BUTTON_PERF && value && !saving && !menu_open)
            init_patch();
        if (index == BUTTON_PRESET && value) {
            typed_digits = 0;
            if (saving) { // cancels a save
                saving = 0;
                display_led(LED_REC, 0);
                idle_display();
            } else if (preset_mode)
                manual_mode();
            else
                load_patch(current_patch);
        }
        if (index == BUTTON_REC && value && !menu_open) {
            typed_digits = 0;
            if (!saving) {
                saving = 1; // the LED blinks in the main loop until the second press
                show_patch(current_patch);
                display_until = 0;
            } else {
                saving = 0;
                display_led(LED_REC, 0);
                display_text(save_patch(current_patch) ? "donE" : "Err");
                display_until = ms_ticks + 1000;
            }
        }
        if (index == BUTTON_TUNE && value)
            tune_requested = 1;
        if (index == BUTTON_SETTINGS && value) {
            menu_open = !menu_open;
            menu_page = -1;
            display_led(LED_SETTINGS, menu_open);
            display_until = 0;
            idle_display();
        }
    }
    if (kind == CONTROL_BUTTON && index <= BUTTON_9 && value && menu_open) {
        int page = index == BUTTON_0 ? PAGE_DRIFT : index - BUTTON_1; // key 1 = first page, key 0 = the tenth
        if (page == PAGE_CYCLE)
            cycle_env = menu_page == PAGE_CYCLE ? (cycle_env + 1) % 4 : synth_env_select() ? synth_env_select() + 1 : 0;
        if (page >= 0 && page < PAGE_COUNT) {
            menu_page = page;
            encoder_steps = 0;
            display_text(page_name(page));
            show_value_at = ms_ticks + 700;
            display_until = 0;
        }
    }
    if (kind == CONTROL_BUTTON && index <= BUTTON_9 && value && !menu_open && (preset_mode || saving)) {
        int digit = index - BUTTON_0;
        if (typed_digits == 1 && ms_ticks - typed_at < 3000) {
            int n = current_patch / 100 * 100 + typed_value * 10 + digit;
            typed_digits = 0;
            if (saving) {
                current_patch = n;
                show_patch(n);
            } else
                load_patch(n);
        } else {
            typed_digits = 1;
            typed_value = digit;
            typed_at = ms_ticks;
            char text[6];
            patch_label(current_patch, text);
            text[3] = (char)('0' + digit);
            text[4] = '-';
            display_text(text);
            display_until = 0;
        }
    }
    if (kind == CONTROL_ENCODER && !menu_open && (preset_mode || saving)) {
        encoder_patch_steps += (int)value;
        if (encoder_patch_steps >= 4 || encoder_patch_steps <= -4) {
            int n = (current_patch + (encoder_patch_steps > 0 ? 1 : PATCH_COUNT - 1)) % PATCH_COUNT;
            encoder_patch_steps = 0;
            typed_digits = 0;
            if (saving) {
                current_patch = n;
                show_patch(n);
            } else if (!load_patch(n)) {
                current_patch = n; // an empty slot: show it, keep the sound
                show_patch(n);
            }
        }
    }
    if (kind == CONTROL_ENCODER && menu_open && menu_page >= 0) {
        encoder_steps += (int)value;
        if (encoder_steps >= 4 || encoder_steps <= -4) { // 4 transitions per quadrature cycle
            int count = setting_count(menu_page);
            int next = (setting_get(menu_page) + (encoder_steps > 0 ? 1 : count - 1)) % count;
            encoder_steps = 0;
            setting_set(menu_page, next);
            show_value_at = 0;
            display_until = 0;
            idle_display();
        }
    }
}

// Settings as the host sees them (7D sets one, 54 reads them all).
typedef enum {
    SET_LFO1_SYNC, SET_LFO1_TRIGGER, SET_LAYERS, SET_SPLIT, SET_KEYTRACK, SET_LFO2_SYNC, SET_LFO2_TRIGGER,
    SET_EDIT_LAYER, SET_LFO_SELECT, SET_SHAPE_L1_LFO1, SET_SHAPE_L1_LFO2, SET_SHAPE_L2_LFO1, SET_SHAPE_L2_LFO2,
    SET_LFO2_DESTS_L1, SET_LFO2_DESTS_L2, SET_CYCLE_L1, SET_CYCLE_L2,
    SET_CYCLE_SYNC, // 8 of them: layer 1 amp, filter, Env 3, Env 4, then layer 2 (0 free, else division + 1)
    SET_BEND = SET_CYCLE_SYNC + 8, SET_DETUNE, // bend range (semitones), unison detune (cents)
    SET_VOICE_L1, SET_VOICE_L2, SET_DRIFT, SET_SCALE, SET_ROOT, // voice modes (mode | priority << 2), drift, scale
    SET_WRITABLE,                                        // read-only from here
    SET_ENV_SELECT = SET_WRITABLE, SET_PRESET_MODE, SET_CURRENT_PATCH, SET_MORPH, SET_COUNT // morph: set with 44
} host_setting_t;

static int host_setting_get(int id)
{
    switch (id) {
    case SET_LFO1_SYNC: case SET_LFO2_SYNC: return (int)lfo_sync[id == SET_LFO2_SYNC];
    case SET_LFO1_TRIGGER: case SET_LFO2_TRIGGER: return (int)lfo_trigger[id == SET_LFO2_TRIGGER];
    case SET_LAYERS: return (int)synth_layer_mode();
    case SET_SPLIT: return synth_split_note();
    case SET_KEYTRACK: return synth_keytrack();
    case SET_EDIT_LAYER: return synth_edit_layer();
    case SET_LFO_SELECT: return synth_lfo_select();
    case SET_SHAPE_L1_LFO1: case SET_SHAPE_L1_LFO2: case SET_SHAPE_L2_LFO1: case SET_SHAPE_L2_LFO2:
        return synth_lfo_shape((id - SET_SHAPE_L1_LFO1) / 2, (id - SET_SHAPE_L1_LFO1) % 2);
    case SET_LFO2_DESTS_L1: case SET_LFO2_DESTS_L2: return synth_lfo2_dests(id - SET_LFO2_DESTS_L1);
    case SET_CYCLE_L1: case SET_CYCLE_L2: return synth_env_cycle(id - SET_CYCLE_L1);
    case SET_CYCLE_SYNC: case SET_CYCLE_SYNC + 1: case SET_CYCLE_SYNC + 2: case SET_CYCLE_SYNC + 3:
    case SET_CYCLE_SYNC + 4: case SET_CYCLE_SYNC + 5: case SET_CYCLE_SYNC + 6: case SET_CYCLE_SYNC + 7:
        return synth_env_sync((id - SET_CYCLE_SYNC) / 4, (id - SET_CYCLE_SYNC) % 4);
    case SET_BEND: return synth_bend_range();
    case SET_DETUNE: return synth_detune();
    case SET_VOICE_L1: case SET_VOICE_L2: return synth_voice_mode(id - SET_VOICE_L1);
    case SET_DRIFT: return synth_drift();
    case SET_SCALE: return synth_scale() & 15;
    case SET_ROOT: return synth_scale() >> 4;
    case SET_MORPH: return synth_morph_target();
    case SET_ENV_SELECT: return synth_env_select();
    case SET_PRESET_MODE: return preset_mode;
    case SET_CURRENT_PATCH: return current_patch;
    default: return 0;
    }
}

static int host_setting_set(int id, int value)
{
    static const uint8_t counts[SET_WRITABLE] = {
        LFO_SYNC_COUNT, LFO_TRIGGER_COUNT, LAYER_MODE_COUNT, 128, 3, LFO_SYNC_COUNT, LFO_TRIGGER_COUNT, 2, 2,
        LFO_SHAPE_COUNT, LFO_SHAPE_COUNT, LFO_SHAPE_COUNT, LFO_SHAPE_COUNT, 8, 8, 16, 16,
        [SET_CYCLE_SYNC ... SET_CYCLE_SYNC + 7] = LFO_DIVISIONS + 1, [SET_BEND] = 13, [SET_DETUNE] = 51,
        [SET_VOICE_L1] = 11, [SET_VOICE_L2] = 11, [SET_DRIFT] = 100, [SET_SCALE] = SCALE_COUNT, [SET_ROOT] = 12,
    };
    if (id < 0 || id >= SET_WRITABLE || value < 0 || value >= counts[id])
        return 0;
    switch (id) {
    case SET_LFO1_SYNC: case SET_LFO2_SYNC: lfo_sync[id == SET_LFO2_SYNC] = (lfo_sync_t)value; break;
    case SET_LFO1_TRIGGER: case SET_LFO2_TRIGGER: lfo_trigger[id == SET_LFO2_TRIGGER] = (lfo_trigger_t)value; break;
    case SET_LAYERS: synth_set_layer_mode((layer_mode_t)value); break;
    case SET_SPLIT: synth_set_split_note(value); break;
    case SET_KEYTRACK: synth_set_keytrack(value); break;
    case SET_EDIT_LAYER: synth_set_edit_layer(value); break;
    case SET_LFO_SELECT: synth_set_lfo_select(value); break;
    case SET_LFO2_DESTS_L1: case SET_LFO2_DESTS_L2: synth_set_lfo2_dests(id - SET_LFO2_DESTS_L1, value); break;
    case SET_CYCLE_L1: case SET_CYCLE_L2: synth_set_env_cycle(id - SET_CYCLE_L1, value); break;
    case SET_CYCLE_SYNC: case SET_CYCLE_SYNC + 1: case SET_CYCLE_SYNC + 2: case SET_CYCLE_SYNC + 3:
    case SET_CYCLE_SYNC + 4: case SET_CYCLE_SYNC + 5: case SET_CYCLE_SYNC + 6: case SET_CYCLE_SYNC + 7:
        synth_set_env_sync((id - SET_CYCLE_SYNC) / 4, (id - SET_CYCLE_SYNC) % 4, value);
        break;
    case SET_BEND: synth_set_bend_range(value); break;
    case SET_DETUNE: synth_set_detune(value); break;
    case SET_VOICE_L1: case SET_VOICE_L2: synth_set_voice_mode(id - SET_VOICE_L1, value); break;
    case SET_DRIFT: synth_set_drift(value); break;
    case SET_SCALE: synth_set_scale(value | (synth_scale() & ~15)); break;
    case SET_ROOT: synth_set_scale((synth_scale() & 15) | value << 4); break;
    default:
        synth_set_lfo_shape((id - SET_SHAPE_L1_LFO1) / 2, (id - SET_SHAPE_L1_LFO1) % 2, value);
        break;
    }
    show_edit_layer();
    return 1;
}

// The sustain pedal, and the keys let go while it was down: the layers each one's note-off releases
static int sustain, bank;
static uint8_t sustained[128];

// After tuning (25 s or so) the USB ring holds only the first packets that came in meanwhile, so note-offs may be
// missing: drop them all, and let go of the keys the sequence holds and of the pedal. (The clock ticks didn't go
// through the ring.)
static void drop_stale_midi(void)
{
    usb_midi_flush();
    din_midi_flush();
    seq_stop();
    sustain = 0;
    for (int n = 0; n < 128; n++)
        sustained[n] = 0;
}

static void run_tuning(void)
{
    synth_all_off();
    display_text("tUnE");
    display_update();
    tuning_run();
    synth_init();
    drop_stale_midi();
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
    if (data_length == 11 && data[0] == 0x6C) {
        dac_config_t c = {.bus = data[1], .mode = data[2], .high = (uint16_t)(data[3] << 7 | data[4]),
                          .low = (uint16_t)(data[5] << 7 | data[6]), .speed = data[7], .sync_us = data[8]};
        int ok = dac_configure(&c);
        if (ok)
            sh_set_timing(data[9], data[10]);
        reply[n++] = 0x7F;
        reply[n++] = 0x6C;
        reply[n++] = (uint8_t)ok;
    }
    if (data_length == 1 && data[0] == 0x6D) {
        const dac_config_t *c = dac_config();
        uint32_t settle, sample;
        sh_timing(&settle, &sample);
        reply[n++] = 0x6D;
        reply[n++] = c->bus;
        reply[n++] = c->mode;
        reply[n++] = (c->high >> 7) & 0x7F;
        reply[n++] = c->high & 0x7F;
        reply[n++] = (c->low >> 7) & 0x7F;
        reply[n++] = c->low & 0x7F;
        reply[n++] = c->speed;
        reply[n++] = c->sync_us;
        reply[n++] = (uint8_t)(settle & 0x7F);
        reply[n++] = (uint8_t)(sample & 0x7F);
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
    if (data_length == 3 && data[0] == 0x7D && host_setting_set(data[1], data[2])) {
        reply[n++] = 0x7F;
        reply[n++] = 0x7D;
    }
    if (data_length == 1 && data[0] == 0x54) {
        reply[n++] = 0x54;
        reply[n++] = SET_COUNT;
        for (int i = 0; i < SET_COUNT; i++) {
            int v = host_setting_get(i);
            reply[n++] = (uint8_t)(v >> 7 & 0x7F);
            reply[n++] = (uint8_t)(v & 0x7F);
        }
    }
    if (data_length == 7 && data[0] == 0x4E && data[1] < 2 && data[2] < 2 && data[3] >= 1 && data[3] <= LFO_MAX_STEPS) {
        int lfo = LFO_ID(data[1], data[2]);
        lfo_step_count[lfo] = data[3];
        lfo_pattern[lfo] = (uint16_t)(data[4] << 14 | data[5] << 7 | data[6]);
        reply[n++] = 0x7F;
        reply[n++] = 0x4E;
    }
    if (data_length == 3 && data[0] == 0x4D && data[1] < 2 && data[2] < 2) {
        int lfo = LFO_ID(data[1], data[2]);
        reply[n++] = 0x4D;
        reply[n++] = data[1];
        reply[n++] = data[2];
        reply[n++] = lfo_step_count[lfo];
        reply[n++] = (uint8_t)(lfo_pattern[lfo] >> 14 & 0x7F);
        reply[n++] = (uint8_t)(lfo_pattern[lfo] >> 7 & 0x7F);
        reply[n++] = (uint8_t)(lfo_pattern[lfo] & 0x7F);
    }
    if (data_length == 3 && data[0] == 0x44 && (data[1] << 7 | data[2]) <= PATCH_COUNT) {
        load_morph(data[1] << 7 | data[2]);
        reply[n++] = 0x7F;
        reply[n++] = 0x44;
    }
    if (data_length == 1 && data[0] == 0x48) {
        const sequence_t *q = &sequence;
        reply[n++] = 0x48;
        reply[n++] = q->length;
        reply[n++] = q->lanes;
        reply[n++] = q->division;
        reply[n++] = (uint8_t)(q->bpm >> 7);
        reply[n++] = q->bpm & 0x7F;
        reply[n++] = q->flags & 0x7F;
        reply[n++] = q->swing;
        reply[n++] = q->smooth & 0x0F;
        reply[n++] = (uint8_t)seq_max_steps(q->lanes);
        reply[n++] = q->arp;
    }
    if (data_length == 10 && data[0] == 0x47) {
        sequence_t *q = &sequence;
        q->length = data[1];
        q->lanes = data[2];
        q->division = data[3];
        q->bpm = (uint8_t)(data[4] << 7 | data[5]);
        q->flags = data[6];
        q->swing = data[7];
        q->smooth = data[8] & 0x0F;
        q->arp = data[9];
        seq_fix();
        if (!(q->flags & SEQ_ENABLED))
            seq_stop();
        reply[n++] = 0x7F;
        reply[n++] = 0x47;
    }
    if (data_length == 3 && data[0] == 0x46 && data[1] < SEQ_MAX_STEPS && data[2] <= 8 && data[1] + data[2] <= SEQ_MAX_STEPS) {
        const sequence_t *q = &sequence;
        reply[n++] = 0x46;
        reply[n++] = data[1];
        reply[n++] = data[2];
        for (int i = data[1]; i < data[1] + data[2]; i++) {
            reply[n++] = q->note[i] & 0x7F;
            reply[n++] = q->velocity[i] & 0x7F;
            reply[n++] = q->gate[i] & 0x7F;
            reply[n++] = q->gate[i] >> 7;
            reply[n++] = q->extra[i] & 0x1F;
            for (int l = 0; l < SEQ_LANES; l++)
                reply[n++] = q->lane[l][i] & 0x7F;
        }
    }
    if (data_length >= 3 && data[0] == 0x45 && data[1] < SEQ_MAX_STEPS && data[2] <= 8 && data[1] + data[2] <= SEQ_MAX_STEPS
        && data_length == 3 + 9 * data[2]) {
        sequence_t *q = &sequence;
        for (int k = 0; k < data[2]; k++) {
            const uint8_t *x = data + 3 + 9 * k;
            int i = data[1] + k, gate = x[2] < 1 ? 1 : x[2] > SEQ_GATE_TIE ? SEQ_GATE_TIE : x[2];
            q->note[i] = x[0];
            q->velocity[i] = x[1];
            q->gate[i] = (uint8_t)(gate | (x[3] ? 0x80 : 0));
            q->extra[i] = x[4] & 0x1F;
            for (int l = 0; l < SEQ_LANES; l++)
                q->lane[l][i] = x[5 + l];
        }
        reply[n++] = 0x7F;
        reply[n++] = 0x45;
    }
    if (data_length == 1 && data[0] == 0x4C) {
        reply[n++] = 0x4C;
        for (int i = 0; i <= SWITCH_SYNC; i++)
            reply[n++] = (uint8_t)synth_switch(i);
    }
    if (data_length == 3 && data[0] == 0x4B && data[1] <= SWITCH_SYNC && data[1] != SWITCH_KEYTRACK_HALF
        && data[1] != SWITCH_KEYTRACK_FULL) {
        synth_set_switch(data[1], data[2]);
        reply[n++] = 0x7F;
        reply[n++] = 0x4B;
    }
    if (data_length == 2 && data[0] == 0x53 && data[1] < 2) {
        reply[n++] = 0x53;
        reply[n++] = data[1];
        reply[n++] = PARAM_COUNT;
        for (int i = 0; i < PARAM_COUNT; i++) {
            int v = (int)(synth_param(data[1], i) * 16383.0f + 0.5f);
            reply[n++] = (uint8_t)(v >> 7);
            reply[n++] = (uint8_t)(v & 0x7F);
        }
    }
    if (data_length == 5 && data[0] == 0x52 && data[1] < 2 && data[2] < PARAM_COUNT) {
        synth_set_param(data[1], data[2], (float)(data[3] << 7 | data[4]) / 16383.0f);
        reply[n++] = 0x7F;
        reply[n++] = 0x52;
    }
    if (data_length >= 3 && data_length <= 3 + PATCH_NAME && data[0] == 0x59) {
        int slot = data[1] << 7 | data[2];
        char name[PATCH_NAME + 1] = {0};
        for (int i = 0; i < data_length - 3; i++)
            name[i] = data[3 + i] >= 0x20 ? (char)data[3 + i] : ' ';
        if (slot < PATCH_COUNT && store_rename(slot, name)) {
            if (slot == current_patch && preset_mode)
                for (int i = 0; i <= PATCH_NAME; i++)
                    loaded_name[i] = name[i];
            reply[n++] = 0x7F;
            reply[n++] = 0x59;
        }
    }
    if (data_length == 3 && data[0] == 0x4F && (data[1] << 7 | data[2]) < PATCH_COUNT) {
        if (store_clear(data[1] << 7 | data[2])) {
            reply[n++] = 0x7F;
            reply[n++] = 0x4F;
        }
    }
    if (data_length == 2 && data[0] == 0x50 && data[1] < FACTORY_COUNT) {
        char name[PATCH_NAME + 1];
        int ok = store_factory_name(data[1], name);
        reply[n++] = 0x50;
        reply[n++] = data[1];
        reply[n++] = (uint8_t)ok;
        for (int i = 0; ok && name[i]; i++)
            reply[n++] = (uint8_t)name[i] & 0x7F;
    }
    if ((data_length == 4 || data_length == 5) && data[0] == 0x51 && data[1] < FACTORY_COUNT
        && (data[2] << 7 | data[3]) < PATCH_COUNT) {
        int slot = data[2] << 7 | data[3], force = data_length == 5 && data[4] == 1;
        int status = !force && store_slot_kind(slot) != SLOT_EMPTY ? 1 : 0;
        if (!status) {
            int r = store_import_factory(data[1], slot);
            status = r > 0 ? 0 : r == 0 ? 2 : 3;
        }
        reply[n++] = 0x51;
        reply[n++] = (uint8_t)status;
    }
    if (data_length >= 5 && data[0] == 0x5A && data[4] >= 1 && data[4] <= 48 && data_length == 5 + 2 * data[4]) {
        uint32_t address = (uint32_t)data[1] << 14 | (uint32_t)data[2] << 7 | data[3];
        uint8_t bytes[48];
        for (int i = 0; i < data[4]; i++)
            bytes[i] = (uint8_t)(data[5 + 2 * i] << 4 | data[6 + 2 * i]);
        if (address + data[4] <= EEPROM_SIZE && eeprom_write(address, bytes, data[4])) {
            reply[n++] = 0x7F;
            reply[n++] = 0x5A;
        }
    }
    if (data_length == 1 && data[0] == 0x5B) {
        synth_all_off();
        sh_set(CV_MASTER_VOL, 0);
        int converted = 0, errors = 0;
        for (int i = 0; i < PATCH_COUNT; i++) {
            int r = store_import_stock(i);
            converted += r > 0;
            errors += r < 0;
            IWDG_KR = IWDG_KR_REFRESH;
            sh_refresh();
        }
        if (!store_finish_import())
            errors++;
        reply[n++] = 0x5B;
        reply[n++] = (uint8_t)(converted >> 7);
        reply[n++] = converted & 0x7F;
        reply[n++] = (uint8_t)(errors > 127 ? 127 : errors);
    }
    if (data_length == 3 && (data[0] == 0x5C || data[0] == 0x5D)) {
        int slot = data[1] << 7 | data[2];
        if (slot < PATCH_COUNT && (data[0] == 0x5C ? load_patch(slot) : save_patch(slot))) {
            reply[n++] = 0x7F;
            reply[n++] = data[0];
        }
    }
    if (data_length == 6 && data[0] == 0x56 && data[1] < 2) {
        int depth = (data[4] << 7 | data[5]) - 99;
        if (synth_set_route(data[1], (mod_source_t)data[2], (mod_dest_t)data[3], depth)) {
            reply[n++] = 0x7F;
            reply[n++] = 0x56;
        }
    }
    if (data_length == 2 && data[0] == 0x57 && data[1] < 2) {
        const route_t *r = synth_routes(data[1]);
        reply[n++] = 0x57;
        reply[n++] = data[1];
        for (int i = 0; i < ROUTES; i++) {
            int d = r[i].depth + 99;
            reply[n++] = r[i].source;
            reply[n++] = r[i].dest;
            reply[n++] = (uint8_t)(d >> 7);
            reply[n++] = d & 0x7F;
        }
    }
    if (data_length == 1 && data[0] == 0x58) {
        uint32_t mhz = system_hz / 1000000u, values[] = {cycles_engine, cycles_refresh, cycles_pass, cycles_pass_max,
                                                         cycles_controls, cycles_display, passes_per_second * mhz};
        reply[n++] = 0x58;
        for (int i = 0; i < 7; i++) {
            uint32_t us = values[i] / mhz;
            reply[n++] = (uint8_t)(us >> 14 & 0x7F);
            reply[n++] = (uint8_t)(us >> 7 & 0x7F);
            reply[n++] = (uint8_t)(us & 0x7F);
        }
        cycles_pass_max = 0;
    }
    if (data_length == 1 && data[0] == 0x5F) {
        init_patch();
        reply[n++] = 0x7F;
        reply[n++] = 0x5F;
    }
    if (data_length == 3 && data[0] == 0x5E && (data[1] << 7 | data[2]) < PATCH_COUNT) {
        int slot = data[1] << 7 | data[2];
        patch_t patch;
        slot_kind_t kind = store_slot_kind(slot);
        reply[n++] = 0x5E;
        reply[n++] = data[1];
        reply[n++] = data[2];
        reply[n++] = (uint8_t)kind;
        if (kind == SLOT_OURS && store_load_patch(slot, &patch))
            for (int i = 0; i < PATCH_NAME && patch.name[i]; i++)
                reply[n++] = (uint8_t)patch.name[i] & 0x7F;
    }
    if (data_length == 5 && data[0] == 0x79 && data[4] >= 1 && data[4] <= 48) {
        uint32_t address = (uint32_t)data[1] << 14 | (uint32_t)data[2] << 7 | data[3];
        uint8_t bytes[48];
        eeprom_read(address, bytes, data[4]);
        uint8_t status = eeprom_status();
        for (int i = 0; i < 5; i++)
            reply[n++] = data[i];
        reply[n++] = status >> 4;
        reply[n++] = status & 0x0F;
        for (int i = 0; i < data[4]; i++) {
            reply[n++] = bytes[i] >> 4;
            reply[n++] = bytes[i] & 0x0F;
        }
    }
    if (data_length == 2 && data[0] == 0x7E && data[1] < 2) {
        synth_set_edit_layer(data[1]);
        show_edit_layer();
        reply[n++] = 0x7F;
        reply[n++] = 0x7E;
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
        drop_stale_midi();
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

// MIDI CCs → parameters of the layer the channel plays (layer 2 for channel 2 in channel mode), else the edit layer.
// Also CC 64 sustain pedal, CC 0 / 32 bank select (for program change).
static const struct { uint8_t cc, param; } cc_map[] = {
    {14, POT_FREQ_A}, {15, POT_FREQ_B}, {16, POT_FINE}, {17, POT_A_PW}, {18, POT_B_PW}, {19, POT_A_LEVEL},
    {20, POT_B_LEVEL}, {21, POT_NOISE}, {74, POT_CUTOFF}, {71, POT_RESONANCE}, {22, POT_FIL_ENV_AMT},
    {23, POT_FIL_ATT}, {24, POT_FIL_DEC}, {25, POT_FIL_SUS}, {26, POT_FIL_REL}, {73, POT_AMP_ATT}, {75, POT_AMP_DEC},
    {70, POT_AMP_SUS}, {72, POT_AMP_REL}, {5, POT_GLIDE}, {76, POT_LFO_FREQ}, {77, POT_LFO_AMOUNT},
    {27, PARAM_LFO2_RATE}, {28, PARAM_LFO2_AMOUNT}, {29, POT_PMOD_FIL_ENV}, {30, POT_PMOD_OSC_B},
    {31, POT_MASTER_TUNE}, {85, PARAM_ENV3_ATT}, {86, PARAM_ENV3_DEC}, {87, PARAM_ENV3_SUS}, {88, PARAM_ENV3_REL},
    {89, PARAM_ENV4_ATT}, {90, PARAM_ENV4_DEC}, {102, PARAM_ENV4_SUS}, {103, PARAM_ENV4_REL},
};
// A key let go: `layers` are the ones its note-off may release (synth_channel_layers())
static void key_off(uint8_t note, int layers)
{
    if (sustain) {
        sustained[note & 0x7F] |= (uint8_t)layers;
        return;
    }
    if (!seq_key_off(note, layers))
        synth_note_off_layers(note, layers);
}

static void control_change(uint8_t cc, uint8_t value, int channel)
{
    if (cc == 64) { // sustain pedal
        sustain = value >= 64;
        if (!sustain)
            for (int n = 0; n < 128; n++)
                if (sustained[n]) {
                    int layers = sustained[n];
                    sustained[n] = 0;
                    key_off((uint8_t)n, layers);
                }
        return;
    }
    if (cc == 0 || cc == 32) {
        bank = value ? 1 : 0;
        return;
    }
    for (unsigned i = 0; i < sizeof cc_map / sizeof cc_map[0]; i++)
        if (cc_map[i].cc == cc) {
            int layer = synth_layer_mode() == LAYER_CHANNEL ? channel == 1 : synth_edit_layer();
            synth_set_param(layer, cc_map[i].param, (float)value / 127.0f);
            return;
        }
}

// The CV refresh with the amp and filter envelopes run in between (synth_envelopes()), so the envelope-driven CVs
// move more often than the engine's pass: the VCAs four times a pass, the cutoffs twice, oscillator pitch and the
// global CVs once. A voice that just started gets its pitch first, so its VCA never opens on the old note; silent
// voices' pitch and cutoff are refreshed only every 8th pass (their S&H only has to hold).
#define ENVELOPE_TICKS 4
static void refresh_cvs(void)
{
    static uint32_t last_tick, count;
    unsigned fresh = synth_take_fresh_voices();
    unsigned voices = ++count % 8 ? synth_active_voices() & ~fresh : 0xFFu;
    sh_refresh_pitch(fresh);
    for (int k = 0; k < ENVELOPE_TICKS; k++) {
        uint32_t now = DWT_CYCCNT;
        float dt = (float)(now - last_tick) / (float)system_hz;
        last_tick = now;
        int cutoff = !(k & 1);
        synth_envelopes(dt, cutoff);
        if (cutoff)
            sh_refresh_cutoff(voices | fresh);
        sh_refresh_vca();
        sh_refresh_pitch(voices & 3u << 2 * k); // two voices' pitch per tick
    }
    sh_refresh_global();
}

// MIDI realtime, straight from the USB and DIN interrupts (usb_midi_realtime(), din_init()): the clock the LFOs and
// the sequence follow
static void realtime(uint8_t status, uint32_t cycles)
{
    if (status == 0xF8)
        lfo_clock_tick(cycles);
    else if (status == 0xFA)
        lfo_clock_start();
    else if (status == 0xFC)
        lfo_clock_stop();
}

static void handle_midi(void)
{
    static uint8_t sysex[SYSEX_MAX];
    static int sysex_length;
    uint8_t p[4];

    while (usb_midi_read(p) || din_midi_read(p)) { // USB first, then the 5-pin socket
        uint8_t cin = p[0] & 0x0F;
        uint8_t status = p[1] & 0xF0;
        switch (cin) {
        case 0x9:
            if (direct_mode)
                break;
            if (p[3] && menu_open && menu_page == PAGE_SPLIT) { // play a note to set the split point
                synth_set_split_note(p[2]);
                show_setting(PAGE_SPLIT);
            } else if (p[3]) { // the sequence takes the key if it's on for that key's layer
                sustained[p[2] & 0x7F] &= (uint8_t)~synth_channel_layers(p[1] & 0x0F);
                if (!seq_key_on(p[2], p[3], synth_key_layers(p[2], p[1] & 0x0F)))
                    synth_note_on(p[2], p[3], p[1] & 0x0F);
            } else
                key_off(p[2], synth_channel_layers(p[1] & 0x0F));
            break;
        case 0x8:
            key_off(p[2], synth_channel_layers(p[1] & 0x0F));
            break;
        case 0xC: // program change: patch n of the bank chosen by bank select (CC 0 / 32: 0 = A, 1 = b)
            if (bank * 100 + p[2] < PATCH_COUNT)
                load_patch(bank * 100 + p[2]);
            break;
        case 0xB:
            if (status == 0xB0 && (p[2] == 120 || p[2] == 123)) { // all sound off, all notes off
                seq_stop();
                synth_all_off();
            }
            if (status == 0xB0 && p[2] == 1)
                synth_mod_wheel(p[3]);
            if (status == 0xB0)
                control_change(p[2], p[3], p[1] & 0x0F);
            break;
        case 0xE:
            synth_pitch_bend(p[2] | p[3] << 7);
            break;
        case 0xD: // channel pressure
            synth_aftertouch(p[2]);
            break;
        case 0xA: // poly pressure: the voices playing that note
            synth_poly_pressure(p[2], p[3]);
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
    eeprom_init();
    controls_init(); // the buttons are read when a patch loads
    store_init();
    tuning_load();
    synth_init();
    seq_clear();
    for (int i = 0; i < 20; i++) // charge every S&H
        sh_refresh();

    panel_gpio_init();
    panel_init();
    display_led(LED_POWER, 1);
    show_edit_layer();

    // No tables (a new EEPROM layout, or a failed write): tune first (about 30 s, silent), showing "tUnE".
    if (!tuning_valid()) {
        display_text("tUnE");
        display_update();
        tuning_run();
        synth_init();
    }
    show_version();
    display_until = ms_ticks + 1500;

    if (store_ready()) {
        current_patch = store_last_patch();
        if (store_preset_mode()) {
            for (uint32_t start = ms_ticks; !controls_ready() && ms_ticks - start < 200;)
                controls_poll(ms_ticks, 0); // read every knob once, so the patch's pickup starts from them
            load_patch(current_patch);
        }
    }
    usb_midi_realtime(realtime);
    din_init(realtime);
    usb_init();

    uint32_t last = DWT_CYCCNT;
    for (;;) {
        IWDG_KR = IWDG_KR_REFRESH;
        handle_midi();

        uint32_t cycles = DWT_CYCCNT;
        float dt = (float)(cycles - last) / (float)system_hz;
        last = cycles;

        uint32_t now = ms_ticks;
        uint32_t tc = DWT_CYCCNT;
        controls_poll(now, control_event);
        cycles_controls = DWT_CYCCNT - tc;
        if (tune_requested) {
            tune_requested = 0;
            run_tuning();
            last = DWT_CYCCNT;
        }
        uint32_t t0 = DWT_CYCCNT;
        if (!direct_mode) {
            seq_update(dt);
            synth_update(dt);
        }
        cycles_engine = DWT_CYCCNT - t0;

        if (display_until && (int32_t)(now - display_until) > 0) {
            display_until = 0;
            idle_display();
        }
        if (show_value_at && (int32_t)(now - show_value_at) > 0) {
            show_value_at = 0;
            idle_display();
        }
        if (saving)
            display_led(LED_REC, (now / 250) & 1);
        if (assigning)
            display_led(LED_ARP_ASSIGN, (now / 250) & 1);
        tc = DWT_CYCCNT;
        if (!report_mode)
            display_update();
        cycles_display = DWT_CYCCNT - tc;

        t0 = DWT_CYCCNT;
        if (direct_mode)
            sh_refresh();
        else
            refresh_cvs();
        cycles_refresh = DWT_CYCCNT - t0;
        cycles_pass = DWT_CYCCNT - cycles;
        if (cycles_pass > cycles_pass_max)
            cycles_pass_max = cycles_pass;
        passes++;
        if (now / 1000 != passes_second) {
            passes_second = now / 1000;
            passes_per_second = passes;
            passes = 0;
        }
    }
}
