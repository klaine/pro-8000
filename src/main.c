// The PRO-800 firmware: the synth engine (synth.c) plays MIDI notes from USB (any channel, or the one set) with the sound set on
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
//   55                     the engine's state (synth_probe(): held keys, voice gates and envelope stages, keytrack,
//                          switch overrides); replies 55 <bytes>; read-only
//   6C <bus> <mode> <h1> <h0> <l1> <l0> <speed> <sync us> <settle> <sample>
//                          probe the CV path (until reboot; dac.h): the DAC80501 bus (bit-bang / SPI3, mode, high
//                          and low cycles or SPI divider, GPIO speed, SYNC wait) and the S&H waits in tenths of a us
//                          (0 = default); acknowledged with 7F 6C <1 if taken>
//   6D                     the probe settings; replies 6D <the same 10 bytes as 6C>
//   6E <0|1>               the DAC80501 setup re-send off/on (0.8.5; on again at reboot); acknowledged 7F 6E
//   6F <reg> <v2> <v1> <v0> write DAC80501 register reg with v2 << 14 | v1 << 7 | v0 (0.8.5); acknowledged 7F 6F
//   75                     run autotune (about 25 s) and save it; replies 76 <rows measured, of 24>
//   77 <row>               tuning table row (osc A 1-8, osc B 1-8, filter 1-8); replies
//                          77 <row> <points> <centihertz, 4 × 7 bits, per point; 0 = not measured>
//   78 <voice> <osc> <note> set voice's oscillator (0 A, 1 B) or filter (2, 0.8.4+) to a MIDI note through the tables
//   7A <0|1>               panel report mode off/on: on sends every control change, and leaves the display to 7B
//                          60 <pot> <v1> <v0>   61 <switch> <0|1>   62 <key byte> <v1> <v0>   63 <64 ± transitions>
//                          64 <button> <0|1>
//   7B <address> <v1> <v0> write a byte of the HT16K33 display RAM (address 0-15)
//   7C                     panel state; replies 7C <26 pots, 2 × 7 bits each> <24 switches, 0/1 each>
//   7D <setting> <value>   set a setting (or 7D <setting> <v1> <v0>, value = v1 << 7 | v0, for values over 127;
//                          host_setting_t: 0 LFO 1 sync, 1 LFO 1 retrigger, 2 layer mode, 3 split
//                          note, 4 keyboard tracking, 5 LFO 2 sync, 6 LFO 2 retrigger, 7 edit layer, 8 selected LFO,
//                          9-12 LFO shapes (layer 1 LFO 1, LFO 2, layer 2 LFO 1, LFO 2), 13-14 LFO 2 routing per layer,
//                          15-16 cycling envelopes per layer, 17-24 synced cycling per envelope: layer 1 amp,
//                          filter, Env 3, Env 4, then layer 2; 0 free, else clock division + 1), 25 bend range
//                          (1-12 semitones), 26 unison detune (0-50 cents), 27-28 voice modes per layer (mode 0 poly
//                          1 mono 2 legato | priority 0 last 1 low 2 high << 2), 29 drift (0-99), 30 scale (0 off,
//                          synth.h SCALE_*), 31 root (0-11), 32-35 LFO 1 delay, LFO 1 fade, LFO 2 delay, LFO 2 fade
//                          (0-99), 36-37 CC A / CC B source numbers (1-127, system-wide), 38-41 macro offsets
//                          (layer 1 macro 1, 2, layer 2 macro 1, 2; the offset + 99), 42 clock source (0 MIDI,
//                          1 internal, 2 SYNC IN), 43 internal tempo (30-300 BPM), 44-45 key-up envelopes per
//                          layer, 46-47 DAHDSR envelopes per layer (a bit per envelope, as cycling), 48-49 envelope
//                          curves per layer (2 bits per envelope: 0 analog, 1 linear, 2 swell), 50-57 envelope delays,
//                          58-65 holds (0-99; layer 1 amp, filter, Env 3, Env 4, then layer 2; all 0.9.0), 66 layer
//                          1's voices in the two-layer modes (1-7, 0 automatic; 0.9.0), 67-74 cycle counts (0
//                          endless, 1-99; in the same order; 0.9.1), 75-76 stage times in note values per layer (a
//                          bit per envelope; 0.9.1), 77 the MIDI channel (0 every channel, 1-16; 0.9.1); read-only
//                          after: env select, preset mode, patch, morph
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
//   5B                     format: wipe every patch slot and mark the EEPROM as ours (format 2); replies 5B 0
//                          <slots> <errors> (was: import the stock patches in place; replies
//                          5B <converted, 2 × 7 bits> <errors>
//   5C <n1> <n0>           load patch n; 5D <n1> <n0> save the current sound to patch n (both acknowledged)
//   5F                     load the init sound (as PERF does; acknowledged)
//   56 <layer> <source> <dest> <d1> <d0>  set a route's depth (d1 << 7 | d0) - 99, 0 removes it; acknowledged
//   58                     timing; replies 58 <engine, refresh, pass, longest pass, panel poll, display, passes/s, ticks: µs,
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
#define SYSEX_MAX 176 // the longest reply: every setting (54), 8 + 2 + 2 × SET_COUNT + 1

static volatile uint32_t ms_ticks;
static int direct_mode;
static uint32_t cycles_engine, cycles_refresh, cycles_pass, cycles_pass_max, cycles_controls, cycles_display; // probe (58)
static uint32_t cycles_ticks; // the pass's synth_tick() calls together (part of cycles_refresh)
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
    // The endpoint takes 16 packets (64 bytes) at a time. A write that can't go out within 20 ms means the host isn't
    // reading (or the cable is out, which the device can't tell without VBUS sensing): drop the message, and the next
    // ones at once until a write goes through again, rather than stall the CV refresh.
    static int stalled;
    for (int sent = 0; sent < count;) {
        int chunk = count - sent > 16 ? 16 : count - sent;
        uint32_t start = ms_ticks;
        while (!usb_midi_write(&packets[4 * sent], chunk))
            if (stalled || ms_ticks - start > 20) {
                stalled = 1;
                return;
            }
        stalled = 0;
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
// digits pick a patch (1-128) and VALUE steps through them; REC, then the slot, then REC saves;
// PERF loads the init sound.
static int current_patch, preset_mode, saving, typed_digits, typed_value, encoder_patch_steps;
static patch_t compare_copy; // Shift + Rec: the edit while the stored patch sounds
static int comparing, undo_valid;
// The sustain pedal, per layer (bits: in channel mode each channel's pedal holds its own layer), and the keys let go
// while it was down: the layers each one's note-off releases
static int sustain;
static uint8_t sustained[128];
static char loaded_name[PATCH_NAME + 1]; // travels with the sound when it's saved to an empty slot
static uint32_t typed_at;

// Step recording (0.9.0): hold Rec and press Seq 1 (the edit layer stays); every MIDI note then writes the next step
// (seq_record_note()) and still plays, without the sequence. Seq 2 writes a rest; Rec or Seq 1 ends it, as does
// loading a patch. The REC LED blinks meanwhile; REC saves the sequence with the patch as usual.
static int recording;
static int rec_used; // this press of Rec ended a recording: Seq 1 doesn't start another until Rec is let go

static void record_end(int show)
{
    if (!recording)
        return;
    recording = 0;
    display_led(LED_REC, 0);
    if (show) {
        display_text(sequence.flags & SEQ_ENABLED ? "donE" : "no");
        display_until = ms_ticks + 1000;
    }
}

static void record_show(int steps)
{
    if (steps < 0) {
        display_text("FULL");
        display_until = ms_ticks + 1000;
        record_end(0);
        return;
    }
    char text[6] = {'S', 't', steps >= 10 ? (char)('0' + steps / 10) : ' ', (char)('0' + steps % 10), 0};
    display_text(text);
    display_until = ms_ticks + 1000;
}

static void patch_label(int n, char *text) // "P  7", "P128": patch n + 1
{
    int p = n + 1;
    text[0] = 'P';
    text[1] = p >= 100 ? (char)('0' + p / 100) : ' ';
    text[2] = p >= 10 ? (char)('0' + p / 10 % 10) : ' ';
    text[3] = (char)('0' + p % 10);
    text[4] = 0;
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

// Shift + Perf: everything a patch load or the init sound replaces (the sound with its morph target, the step
// sequence, the name, the slot and preset mode), swapped back and forth
typedef struct {
    patch_t patch;
    sequence_t sequence;
    char name[PATCH_NAME + 1];
    int slot, preset_mode;
} undo_state_t;
static undo_state_t undo_state, undo_swap; // static: too big for the stack

static void undo_capture(undo_state_t *u)
{
    synth_capture_patch(&u->patch);
    u->sequence = sequence;
    for (int i = 0; i <= PATCH_NAME; i++)
        u->name[i] = loaded_name[i];
    u->slot = current_patch;
    u->preset_mode = preset_mode;
}

static void undo_remember(void) // before a load or the init sound replaces the sound
{
    undo_capture(&undo_state);
    undo_valid = 1;
}

static void undo_swap_back(void)
{
    record_end(0);
    seq_stop();
    undo_capture(&undo_swap);
    synth_apply_patch(&undo_state.patch);
    load_morph(undo_state.patch.morph);
    sequence = undo_state.sequence;
    seq_fix();
    for (int i = 0; i <= PATCH_NAME; i++)
        loaded_name[i] = undo_state.name[i];
    current_patch = undo_state.slot;
    preset_mode = undo_state.preset_mode;
    display_led(LED_PRESET, preset_mode);
    if (preset_mode)
        store_set_last(current_patch, 1);
    undo_state = undo_swap; // pressed again: redo
}

static void init_patch(void);

// Loads slot n. An empty slot gives the init sound there (so it starts from scratch, and REC saves it in place); a
// slot that isn't one of ours shows nonE and keeps the sound.
static int load_patch(int n)
{
    patch_t patch;
    if (!store_load_patch(n, &patch)) {
        if (store_slot_kind(n) == SLOT_EMPTY) {
            init_patch();
            current_patch = n;
            store_set_last(n, 1);
            return 1;
        }
        display_text("nonE");
        display_until = ms_ticks + 1000;
        return 0;
    }
    record_end(0);
    seq_stop();
    undo_remember();
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
    undo_remember();
    record_end(0);
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
// Key 6 sets one envelope of the edit layer (pressing 6 again moves to the next envelope): its mode (ADSR, DAHDSR,
// cycling free or synced), and Arp Up-Dn steps on to its trigger (key down or key up), its timing (seconds, or note
// values at the tempo), its curve and its count of cycles. While the page is open, the ATT and DEC knobs set the envelope's delay and hold (DAHDSR) instead of the
// attack and decay. Arp Up-Dn on key 3 switches between the layer mode and how the two-layer modes share the voices
// (layer 1 + layer 2, or automatic).
enum { PAGE_LFO_SYNC, PAGE_LFO_TRIGGER, PAGE_LAYERS, PAGE_SPLIT, PAGE_KEYTRACK, PAGE_CYCLE, PAGE_BEND, PAGE_DETUNE,
       PAGE_VOICE, PAGE_DRIFT, PAGE_COUNT }; // keys 1-9, then 0

static const char page_names[PAGE_COUNT][5] = {"SYnC", "trIG", "LAYr", "SPLt", "trAC", "", "bEnd", "dEtU", "UOIC",
                                                "drFt"};
// Page 6's values for an envelope, in Arp Up-Dn's order
enum { ENV_PAGE_MODE, ENV_PAGE_TRIGGER, ENV_PAGE_TIME, ENV_PAGE_CURVE, ENV_PAGE_COUNT, ENV_PAGES };
static const char cycle_names[ENV_PAGES][4][6] = {{"En.A", "En.F", "En.3", "En.4"}, {"tr.A", "tr.F", "tr.3", "tr.4"},
                                                  {"tI.A", "tI.F", "tI.3", "tI.4"}, {"CU.A", "CU.F", "CU.3", "CU.4"},
                                                  {"Cn.A", "Cn.F", "Cn.3", "Cn.4"}};
static int cycle_env;  // the envelope page 6 shows (amp, filter, Env 3, Env 4)
static int alt_page;   // pages 3 and 6: the value Arp Up-Dn has chosen: the voice share; ENV_PAGE_*
// Page 6's modes: ADSR, DAHDSR, then cycling (free, then each clock division)
enum { ENV_MODE_CYCLE = 2, ENV_MODE_COUNT = ENV_MODE_CYCLE + 1 + LFO_DIVISIONS };

static const char *page_name(int page)
{
    if (page == PAGE_CYCLE)
        return cycle_names[alt_page][cycle_env];
    if (page == PAGE_LAYERS && alt_page)
        return "Lr.Uo";
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
                                           ENV_MODE_COUNT,
                                           12, 51,            // bend 1-12 semitones, detune 0-50 cents
                                           3, 100};           // the edit layer's voice mode, drift 0-99
    if (page == PAGE_LAYERS && alt_page)
        return VOICE_COUNT; // automatic, then layer 1's 1-7
    if (page == PAGE_CYCLE && alt_page) {
        static const int env_counts[ENV_PAGES] = {[ENV_PAGE_TRIGGER] = 2, [ENV_PAGE_TIME] = 2, [ENV_PAGE_CURVE] = ENV_CURVE_COUNT,
                                                  [ENV_PAGE_COUNT] = 100};
        return env_counts[alt_page];
    }
    return counts[page];
}

static int setting_get(int page)
{
    switch (page) {
    case PAGE_LFO_SYNC: return (int)lfo_sync[synth_lfo_select()];
    case PAGE_LFO_TRIGGER: return (int)lfo_trigger[synth_lfo_select()];
    case PAGE_LAYERS: return alt_page ? synth_layer_voices() : (int)synth_layer_mode();
    case PAGE_KEYTRACK: return synth_keytrack();
    case PAGE_BEND: return synth_bend_range() - 1;
    case PAGE_DETUNE: return synth_detune();
    case PAGE_VOICE: return synth_voice_mode(synth_edit_layer()) & 3;
    case PAGE_DRIFT: return synth_drift();
    case PAGE_CYCLE: {
        int layer = synth_edit_layer();
        if (alt_page == ENV_PAGE_TRIGGER)
            return synth_env_trigger(layer) >> cycle_env & 1;
        if (alt_page == ENV_PAGE_TIME)
            return synth_env_tempo(layer) >> cycle_env & 1;
        if (alt_page == ENV_PAGE_CURVE)
            return synth_env_curve(layer) >> 2 * cycle_env & 3;
        if (alt_page == ENV_PAGE_COUNT)
            return synth_env_count(layer, cycle_env);
        if (synth_env_cycle(layer) >> cycle_env & 1)
            return ENV_MODE_CYCLE + synth_env_sync(layer, cycle_env);
        return synth_env_dahdsr(layer) >> cycle_env & 1;
    }
    default: return synth_split_note();
    }
}

static void setting_set(int page, int value)
{
    switch (page) {
    case PAGE_LFO_SYNC: lfo_sync[synth_lfo_select()] = (lfo_sync_t)value; break;
    case PAGE_LFO_TRIGGER: lfo_trigger[synth_lfo_select()] = (lfo_trigger_t)value; break;
    case PAGE_LAYERS:
        if (alt_page)
            synth_set_layer_voices(value);
        else
            synth_set_layer_mode((layer_mode_t)value);
        break;
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
        int layer = synth_edit_layer(), bit = 1 << cycle_env;
        if (alt_page == ENV_PAGE_TRIGGER) {
            int trigger = synth_env_trigger(layer);
            synth_set_env_trigger(layer, value ? trigger | bit : trigger & ~bit);
            break;
        }
        if (alt_page == ENV_PAGE_TIME) {
            int tempo = synth_env_tempo(layer);
            synth_set_env_tempo(layer, value ? tempo | bit : tempo & ~bit);
            break;
        }
        if (alt_page == ENV_PAGE_CURVE) {
            int bits = synth_env_curve(layer) & ~(3 << 2 * cycle_env);
            synth_set_env_curve(layer, bits | value << 2 * cycle_env);
            break;
        }
        if (alt_page == ENV_PAGE_COUNT) {
            synth_set_env_count(layer, cycle_env, value);
            break;
        }
        if (value < ENV_MODE_CYCLE) { // ADSR or DAHDSR; cycling off
            int dahdsr = synth_env_dahdsr(layer);
            synth_set_env_dahdsr(layer, value ? dahdsr | bit : dahdsr & ~bit);
        }
        int cycle = synth_env_cycle(layer);
        synth_set_env_cycle(layer, value >= ENV_MODE_CYCLE ? cycle | bit : cycle & ~bit);
        synth_set_env_sync(layer, cycle_env, value > ENV_MODE_CYCLE ? value - ENV_MODE_CYCLE : 0);
        break;
    }
    default: synth_set_split_note(value); break;
    }
}

static void show_setting(int page)
{
    _Static_assert(LAYER_MODE_COUNT == 6 && LFO_TRIGGER_COUNT <= 6, "the settings' value names");
    static const char *const values[][6] = {
        {"FrEE", "CLoC"}, {"FrEE", "1St", "EACH", "PoLY"}, {"OFF", "SPLt", "StAC", "CHAn", "8CH", "nPE"}, {0},
        {"OFF", "HALF", "FULL"},
    };
    if (page == PAGE_CYCLE) {
        static const char modes[ENV_MODE_CYCLE + 1][5] = {"AdSr", "dAHd", "CYCL"};
        static const char curves[ENV_CURVE_COUNT][5] = {"AnLG", "LIn", "SUEL"};
        int v = setting_get(page);
        if (alt_page == ENV_PAGE_TRIGGER)
            display_text(v ? "UP" : "dn");
        else if (alt_page == ENV_PAGE_TIME)
            display_text(v ? "SYnC" : "SEC");
        else if (alt_page == ENV_PAGE_CURVE)
            display_text(curves[v]);
        else if (alt_page == ENV_PAGE_COUNT) {
            if (v)
                display_number(v);
            else
                display_text("LOOP");
        } else
            display_text(v > ENV_MODE_CYCLE ? lfo_division_name(v - ENV_MODE_CYCLE - 1) : modes[v]);
    } else if (page == PAGE_LAYERS && alt_page) {
        int n = synth_layer_voices();
        char text[5] = {(char)('0' + n), '-', (char)('0' + VOICE_COUNT - n), 0};
        display_text(n ? text : "AUto");
    } else if (page == PAGE_SPLIT)
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

// MOD ASSIGN (the Arp Assign button), in three stages. Press: SrC. The source: a number key, a touch of a knob that
// stands for one (the LFO knobs for the selected LFO, the ADSR knobs for their envelope), or VALUE scrolls them all
// and Arp Assign (or touching a destination) takes the one shown. The destination: touch its knob or an LFO routing
// button, or VALUE scrolls them (names only) and Arp Assign takes the one shown; the knob last touched before
// pressing is offered, so that stage is skipped. The depth: VALUE, for the edit layer; Arp Assign leaves. The knobs
// are in pickup meanwhile, and a toggled button keeps its routing.
// Hold Arp Assign (0.6 s): browse the edit layer's routes, each shown as source, destination and depth in turn,
// VALUE steps through them by number (nod1, nod2 …) and, once it rests, the shown route's number, source,
// destination and depth cycle; a short press edits the one shown, holding again leaves. With no routes to browse, a
// hold starts MOD ASSIGN as a press does (it used to show nonE and leave, which read as the button not working).
static int assigning, assign_source, assign_dest = DEST_NONE, assign_steps;
static int assign_confirmed; // the destination is settled (touched, or taken with Arp Assign): VALUE sets the depth
static int assign_source_taken; // the source is settled; until then VALUE scrolls the sources
// The knob that picked the source (-1 none) and when it last moved: a knob that stands for a source and a
// destination at once (ADSR, LFO) keeps sending while it's turned, and those mustn't take it as the destination too.
// It counts as a destination only after resting a second.
static int assign_source_pot = -1;
static uint32_t assign_source_pot_at;
static void idle_display(void); // what the display shows when nothing transient is on it
static int browsing, browse_index, browse_frame = -1;
static uint32_t browse_rest_at; // when VALUE last moved in browsing (+ 800 ms): from then the route's parts cycle
static uint32_t assign_press_at; // ms_ticks when Arp Assign went down, 0 = up or the hold has fired
static int last_pot = -1;        // the knob last touched outside MOD ASSIGN
static uint32_t last_pot_at;     // and when: only a knob touched in the 5 s before Arp Assign is offered

static const char source_names[SRC_COUNT][5] = {
    "", "LFO1", "FEnV", "AEnV", "VEL", "nod", "PrES", "EnV3", "EnV4", "rAnd", "notE", "LFO2",
    "SEqA", "SEqb", "SEqC", "SEqd", "L1.U", "L2.U", "GAtE", "tInb",
    "bEnd", "PEdL", "CC.A", "CC.b", "VoIC", "FIX", "nAC1", "nAC2",
};
static const char dest_names[DEST_COUNT][6] = {
    "P.A", "P.b", "PIt", "CUt", "F.EA", "P.EA", "L.An", "L.Fr", "L.FL", "VCA",
    "A.LE", "b.LE", "noIS", "rES", "A.PU", "b.PU", "P.ob", "L.PU",
    "L.rt", "L2.An", "L2.rt", "L2.Fr", "L2.FL", "L2.PU", "norP",
    "EA-A", "EA-d", "EA-S", "EA-r", "EF-A", "EF-d", "EF-S", "EF-r",
    "E3-A", "E3-d", "E3-S", "E3-r", "E4-A", "E4-d", "E4-S", "E4-r",
    "GLId", "dEtU", "VOL", "nAC1", "nAC2",
};

static void show_route(void)
{
    display_number(synth_route(synth_edit_layer(), (mod_source_t)assign_source, (mod_dest_t)assign_dest));
}

// The edit layer's routes in use: their slots, in order. Returns how many.
static int route_slots(int *slots)
{
    const route_t *r = synth_routes(synth_edit_layer());
    int n = 0;
    for (int i = 0; i < ROUTES; i++)
        if (r[i].source != SRC_NONE && r[i].source < SRC_COUNT && r[i].dest < DEST_COUNT)
            slots[n++] = i;
    return n;
}

// Which part of the browsed route shows now: its number while VALUE turns (and 0.8 s after), then number,
// source, destination and depth, 0.6 s each
static int browse_frame_now(void)
{
    int32_t rest = (int32_t)(ms_ticks - browse_rest_at);
    return rest < 0 ? 0 : (int)((uint32_t)rest / 600 % 4);
}

static void browse_show(void)
{
    int slots[ROUTES], n = route_slots(slots);
    if (!n) {
        display_text("nonE");
        return;
    }
    if (browse_index >= n)
        browse_index = n - 1;
    const route_t *r = &synth_routes(synth_edit_layer())[slots[browse_index]];
    int frame = browse_frame_now(), k = browse_index + 1;
    browse_frame = frame;
    if (frame == 0) { // nod1 … nod9, no10 … no16 ("mod", as far as 7 segments go)
        char text[5] = {'n', 'o', k < 10 ? 'd' : (char)('0' + k / 10), (char)('0' + k % 10), 0};
        display_text(text);
    } else if (frame == 1)
        display_text(source_names[r->source]);
    else if (frame == 2)
        display_text(dest_names[r->dest]);
    else
        display_number(r->depth);
}

static void assign_end(void)
{
    assigning = 0;
    browsing = 0;
    synth_freeze_knobs(0);
    display_led(LED_ARP_ASSIGN, 0);
    display_until = 0;
    idle_display(); // the patch number again, not the last route text
}

static void assign_begin(int source, int dest)
{
    browsing = 0;
    assigning = 1;
    assign_source = source;
    assign_source_taken = source != SRC_NONE;
    assign_source_pot = -1;
    assign_dest = dest; // with no source yet: the destination offered (the knob touched last)
    assign_confirmed = dest != DEST_NONE && source != SRC_NONE;
    assign_steps = 0;
    synth_freeze_knobs(1);
    if (!source)
        display_text("SrC");
    else if (dest == DEST_NONE)
        display_text(source_names[source]);
    else {
        display_text(dest_names[dest]);
        show_value_at = ms_ticks + 700;
    }
    display_until = 0;
}

// MOD ASSIGN from the start: SrC, with the knob touched last offered as the destination
static void assign_start(void)
{
    int recent = last_pot >= 0 && ms_ticks - last_pot_at < 5000;
    assign_begin(SRC_NONE, recent ? synth_dest_for_pot(last_pot) : DEST_NONE);
}

static void browse_begin(void)
{
    int slots[ROUTES];
    if (!route_slots(slots)) {
        assign_start();
        return;
    }
    browsing = 1;
    browse_index = 0;
    browse_frame = -1;
    browse_rest_at = ms_ticks + 800;
    display_led(LED_ARP_ASSIGN, 1);
    display_until = 0;
    browse_show();
}

// The source is settled: on to the destination, or, with one offered or already chosen, straight to the depth
static void assign_take_source(int source)
{
    // a destination taken, or offered before the source (the knob touched just before), goes on to the depth; one
    // only scrolled to in the destination stage stays shown, still to be taken
    int ready = assign_dest != DEST_NONE && (assign_confirmed || !assign_source_taken);
    assign_source = source;
    assign_source_taken = 1;
    assign_steps = 0;
    if (ready) {
        assign_confirmed = 1;
        display_text(dest_names[assign_dest]);
        show_value_at = ms_ticks + 700; // then its depth
    } else
        display_text(source_names[source]);
    display_until = 0;
}

// The Arp Assign button: a short press (on release) or a hold (from the main loop)
static void assign_short_press(void)
{
    if (browsing) { // edit the route shown
        int slots[ROUTES], n = route_slots(slots);
        if (!n)
            return;
        const route_t *r = &synth_routes(synth_edit_layer())[slots[browse_index < n ? browse_index : n - 1]];
        assign_begin(r->source, r->dest);
    } else if (assigning && assign_source && !assign_source_taken) {
        assign_take_source(assign_source); // the scrolled source
    } else if (assigning && assign_source_taken && assign_dest != DEST_NONE && !assign_confirmed) {
        assign_confirmed = 1; // take the scrolled destination: VALUE now sets the depth
        show_route();
    } else if (assigning) {
        assign_end();
    } else
        assign_start();
}

static void assign_hold(void)
{
    if (browsing || assigning)
        assign_end();
    else
        browse_begin();
}

// Handles a control event while assigning or browsing. Returns 1 if it was used.
static int assign_event(control_kind_t kind, int index, int32_t value)
{
    if (browsing) {
        if (kind == CONTROL_ENCODER) {
            assign_steps += (int)value;
            browse_rest_at = ms_ticks + 800; // the number while it turns
            if (assign_steps >= 4 || assign_steps <= -4) { // a quadrature cycle per route, as patches and menus
                int slots[ROUTES], n = route_slots(slots);
                browse_index = n ? (browse_index + (assign_steps > 0 ? 1 : n - 1)) % n : 0;
                assign_steps = 0;
                browse_frame = -1;
                browse_show();
            } else if (browse_frame)
                browse_show();
            return 1;
        }
        return kind == CONTROL_POT; // knobs are frozen; buttons work as usual
    }
    if (kind == CONTROL_BUTTON && index <= BUTTON_9 && value) {
        int source = index == BUTTON_0 ? SRC_NOTE : index - BUTTON_0;
        if (source == SRC_LFO) // key 1: the selected LFO; pressed again, the other one
            source = assign_source == SRC_LFO ? SRC_LFO2 : assign_source == SRC_LFO2 ? SRC_LFO
                   : synth_lfo_select() ? SRC_LFO2 : SRC_LFO;
        assign_take_source(source);
        return 1;
    }
    if (kind == CONTROL_POT && index == assign_source_pot) {
        int still = ms_ticks - assign_source_pot_at >= 1000;
        assign_source_pot_at = ms_ticks;
        if (!still)
            return 1; // still the turn that picked the source
        assign_source_pot = -1;
    }
    // In the source stage a knob that stands for a source picks it; any other knob is a destination, which takes
    // the scrolled source along with it (below), or, with none shown, asks for one
    if (kind == CONTROL_POT && !assign_source_taken && synth_source_for_pot(index) != SRC_NONE) {
        if (!assign_confirmed && assign_dest == synth_dest_for_pot(index))
            assign_dest = DEST_NONE; // the knob offered as the destination picked the source: choose one
        assign_take_source(synth_source_for_pot(index));
        assign_source_pot = index;
        assign_source_pot_at = ms_ticks;
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
        assign_source_taken = 1; // a scrolled source, taken by touching its destination
        if (dest != assign_dest || !assign_confirmed) {
            assign_dest = dest;
            assign_confirmed = 1;
            assign_steps = 0;
            display_text(dest_names[dest]);
            show_value_at = ms_ticks + 700;
        }
        return 1;
    }
    if (kind == CONTROL_ENCODER) {
        // scrolling sources and destinations: a quadrature cycle per item, as patches and menus; the depth a step
        // per half cycle, since -99..99 takes a few turns
        int depth_stage = assign_source_taken && assign_dest != DEST_NONE && assign_confirmed;
        int per = depth_stage ? 2 : 4;
        assign_steps += (int)value;
        if (assign_steps < per && assign_steps > -per)
            return 1;
        int step = assign_steps > 0 ? 1 : -1;
        assign_steps = 0;
        if (!assign_source_taken) { // scroll the sources, until one is taken
            assign_source = (assign_source + step + SRC_COUNT) % SRC_COUNT;
            if (!assign_source)
                assign_source = step > 0 ? 1 : SRC_COUNT - 1;
            display_text(source_names[assign_source]);
            display_until = 0;
        } else if (!depth_stage) { // scroll the destinations (names only), until one is taken
            assign_dest = assign_dest == DEST_NONE ? (step > 0 ? 0 : DEST_COUNT - 1)
                                                    : (assign_dest + step + DEST_COUNT) % DEST_COUNT;
            assign_confirmed = 0;
            display_text(dest_names[assign_dest]);
            display_until = 0;
            show_value_at = 0;
        } else { // the depth: a step per half cycle, -99..99 takes a few turns
            int layer = synth_edit_layer();
            int depth = synth_route(layer, (mod_source_t)assign_source, (mod_dest_t)assign_dest) + step;
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

// Shift (the Sync Source button): held, or latched with two quick presses (LED on). The filter and amp ADSR knobs
// edit Env 3 and Env 4 meanwhile (synth.c); the other knobs nudge a secondary value by how far they're turned:
// LFO FREQ / AMOUNT the selected LFO's delay / fade, GLIDE drift, M TUNE unison detune, FINE the bend range,
// CUTOFF / RESONANCE the macro offsets, A / B LEVEL the CC A / B numbers. Shift + Rec compares (the stored patch
// while held), + Perf undoes the last load, + Preset saves to the current slot, + Seq 1 / 2 copies the edit layer
// there, + a number key opens that Settings page, + 0 is a panic.
static int shift_held, shift_latched;
static uint32_t sync_press_at; // the Sync Clock button: when it went down (0 = up, or the hold has fired)
static int adsr_pot_index(int pot) { return (pot >= POT_AMP_REL && pot <= POT_AMP_ATT) || (pot >= POT_FIL_REL && pot <= POT_FIL_ATT); }
static uint32_t shift_tap_at;
static int shift_pot_last[POT_COUNT];
static float shift_acc[POT_COUNT];
static uint32_t shift_value_at; // when to show the secondary's value (after its name)
static int shift_value, shift_pot = -1;

static void shift_set(int on)
{
    synth_set_shift(on);
    display_led(LED_SYNC_SOURCE, on);
    for (int i = 0; i < POT_COUNT; i++)
        shift_pot_last[i] = -1;
    shift_pot = -1;
}

static int shift_active(void) { return shift_held || shift_latched; }

// The MIDI channel the synth listens on: 1-16, 0 every channel (Shift + NOISE, or the host's setting)
static int midi_channel;

static void set_midi_channel(int channel)
{
    if (channel == midi_channel)
        return;
    midi_channel = channel;
    store_set_midi_channel(channel);
    synth_all_off(); // a key held on a channel no longer heard would never be let go
}

static void show_shift_value(void)
{
    if (shift_pot == POT_NOISE && !shift_value)
        display_text("ALL");
    else
        display_number(shift_value);
}

// A knob turned under Shift: the secondary it nudges, by `delta` of 4095
static void shift_turn(int pot, int delta)
{
    static const struct { uint8_t pot; char name[5]; int16_t low, high; } table[] = {
        {POT_LFO_FREQ, "dLY", 0, 99},    {POT_LFO_AMOUNT, "FAdE", 0, 99}, {POT_GLIDE, "drFt", 0, 99},
        {POT_MASTER_TUNE, "dEtU", 0, 50}, {POT_FINE, "bEnd", 1, 12},      {POT_CUTOFF, "nAC1", -99, 99},
        {POT_RESONANCE, "nAC2", -99, 99}, {POT_A_LEVEL, "CC.A", 1, 127},   {POT_B_LEVEL, "CC.b", 1, 127},
        {POT_NOISE, "CHnL", 0, 16},
    };
    unsigned t = 0;
    for (; t < sizeof table / sizeof table[0] && table[t].pot != pot; t++)
        ;
    if (t == sizeof table / sizeof table[0])
        return;
    int which = synth_lfo_select(), layer = synth_edit_layer(), value;
    switch (pot) {
    case POT_LFO_FREQ: value = lfo_delay[which]; break;
    case POT_LFO_AMOUNT: value = lfo_fade[which]; break;
    case POT_GLIDE: value = synth_drift(); break;
    case POT_MASTER_TUNE: value = synth_detune(); break;
    case POT_FINE: value = synth_bend_range(); break;
    case POT_CUTOFF: value = synth_macro_offset(layer, 0); break;
    case POT_RESONANCE: value = synth_macro_offset(layer, 1); break;
    case POT_A_LEVEL: value = synth_cc_number(0); break;
    case POT_NOISE: value = midi_channel; break;
    default: value = synth_cc_number(1); break;
    }
    shift_acc[pot] += (float)delta * (float)(table[t].high - table[t].low) / 4095.0f;
    int step = (int)shift_acc[pot];
    shift_acc[pot] -= (float)step;
    value += step;
    value = value < table[t].low ? table[t].low : value > table[t].high ? table[t].high : value;
    switch (pot) {
    case POT_LFO_FREQ: lfo_delay[which] = (uint8_t)value; break;
    case POT_LFO_AMOUNT: lfo_fade[which] = (uint8_t)value; break;
    case POT_GLIDE: synth_set_drift(value); break;
    case POT_MASTER_TUNE: synth_set_detune(value); break;
    case POT_FINE: synth_set_bend_range(value); break;
    case POT_CUTOFF: synth_set_macro_offset(layer, 0, value); break;
    case POT_RESONANCE: synth_set_macro_offset(layer, 1, value); break;
    case POT_A_LEVEL: synth_set_cc_number(0, value); store_set_cc_number(0, value); break;
    case POT_NOISE: set_midi_channel(value); break;
    default: synth_set_cc_number(1, value); store_set_cc_number(1, value); break;
    }
    shift_value = value;
    if (pot != shift_pot) { // its name first, then the value
        shift_pot = pot;
        display_text(table[t].name);
        display_until = 0;
        shift_value_at = ms_ticks + 500;
    } else if (!shift_value_at) {
        show_shift_value();
        display_until = ms_ticks + 1500;
    }
}

static void compare_end(void)
{
    if (!comparing)
        return;
    comparing = 0;
    synth_apply_patch(&compare_copy);
    display_text("EdIt");
    display_until = ms_ticks + 800;
}

// The clock source: MIDI clock as it comes, or our own 24 ppq generator at `clock_bpm` (the Sync Clock button: taps
// set the tempo, a hold restarts the bar; Shift + Sync Clock cycles the source), or the generator with its tempo and
// phase taken from the SYNC IN jack's pulses (2 per quarter note, as Korg's). The generator stamps each tick with its
// scheduled time, so the clock is as steady as the crystal whatever the main loop is doing.
enum { CLOCK_MIDI, CLOCK_INTERNAL, CLOCK_SYNC_IN };
static int clock_source;
static float clock_bpm = 120.0f;
static uint32_t clock_next, clock_period;
static int clock_primed;
static uint32_t tap_at[4];
static int taps;
static uint32_t sync_pulse_at;

static void clock_set_bpm(float bpm)
{
    clock_bpm = bpm < 30 ? 30 : bpm > 300 ? 300 : bpm;
    clock_period = (uint32_t)((float)system_hz * 60.0f / (clock_bpm * 24.0f));
    store_set_clock(clock_source, (int)(clock_bpm * 10.0f + 0.5f));
}

static void clock_restart(void) // the next tick is the downbeat, now
{
    lfo_clock_start();
    clock_next = DWT_CYCCNT;
    clock_primed = 1;
}

// Runs the generator up to now: from the main loop, before each tick of the refresh
static void clock_run(void)
{
    if (clock_source == CLOCK_MIDI)
        return;
    uint32_t now = DWT_CYCCNT;
    if (!clock_primed) {
        clock_next = now;
        clock_primed = 1;
    }
    for (int n = 0; (int32_t)(now - clock_next) >= 0 && n < 4; n++) {
        lfo_clock_tick(clock_next);
        clock_next += clock_period;
    }
}

static void clock_tap(void)
{
    uint32_t now = ms_ticks;
    if (taps && now - tap_at[taps - 1] > 2000)
        taps = 0; // a new series
    if (taps == 4) {
        for (int i = 0; i < 3; i++)
            tap_at[i] = tap_at[i + 1];
        taps = 3;
    }
    tap_at[taps++] = now;
    if (taps >= 2) {
        float interval = (float)(tap_at[taps - 1] - tap_at[0]) / (float)(taps - 1) / 1000.0f;
        clock_set_bpm(60.0f / interval);
        clock_next = DWT_CYCCNT; // the tap is on the beat
    }
    display_text("tAP");
    display_until = 0;
    shift_value = (int)(clock_bpm + 0.5f);
    shift_value_at = ms_ticks + 500;
}

static void clock_set_source(int source)
{
    clock_source = source;
    clock_primed = 0;
    store_set_clock(clock_source, (int)(clock_bpm * 10.0f + 0.5f));
}

static void sync_in_pulse(void) // a rising edge on the jack: 2 pulses per quarter note
{
    uint32_t now = DWT_CYCCNT, since = now - sync_pulse_at;
    sync_pulse_at = now;
    float s = (float)since / (float)system_hz;
    if (s > 0.04f && s < 1.0f) { // 30-750 BPM at 2 ppq
        clock_bpm = 30.0f / s;
        clock_period = (uint32_t)((float)system_hz * 60.0f / (clock_bpm * 24.0f));
    }
    clock_next = now; // the pulse is a tick
    clock_primed = 1;
}

// What the display shows when nothing transient is on it.
static void idle_display(void)
{
    if (browsing)
        browse_show();
    else if (assigning && assign_source_taken && assign_dest != DEST_NONE && assign_confirmed)
        show_route();
    else if (assigning && assign_source_taken && assign_dest != DEST_NONE)
        display_text(dest_names[assign_dest]); // scrolled, not taken yet
    else if (assigning)
        display_text(assign_source ? source_names[assign_source] : "SrC");
    else if (menu_open && menu_page >= 0)
        show_setting(menu_page);
    else if (menu_open)
        display_text("SEt");
    else if (recording)
        display_text("rSEq");
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

// The envelope page (Settings 6): ATT nudges the page's envelope's delay and DEC its hold (0-99) by how far they're
// turned, shown by name and then as a time. The engine keeps those knobs frozen meanwhile (the main loop).
#define ENV_EXTRA_POTS (1u << POT_AMP_ATT | 1u << POT_AMP_DEC | 1u << POT_FIL_ATT | 1u << POT_FIL_DEC)
static uint32_t extra_frozen;
static int extra_last[POT_COUNT], extra_pot = -1;
static float extra_acc;
static uint32_t extra_value_at; // when to show the value (after its name)

static void show_env_extra(void)
{
    int layer = synth_edit_layer(), hold = extra_pot == POT_AMP_DEC || extra_pot == POT_FIL_DEC;
    int value = hold ? synth_env_hold(layer, cycle_env) : synth_env_delay(layer, cycle_env);
    char text[8];
    format_time(synth_env_extra_time(value), text);
    // in note values when the envelope's stage times are (synced cycling has no delay or hold to show)
    display_text(synth_env_tempo(layer) >> cycle_env & 1 ? synth_env_note_name(synth_env_note_step((float)value / 99.0f))
                                                         : text);
    display_until = ms_ticks + 1500;
}

static void env_extra_turn(int pot, int raw)
{
    int last = extra_last[pot];
    extra_last[pot] = raw;
    if (last < 0)
        return; // the first reading on this page: where the knob is
    int layer = synth_edit_layer(), hold = pot == POT_AMP_DEC || pot == POT_FIL_DEC;
    if (pot != extra_pot)
        extra_acc = 0;
    extra_acc += (float)(raw - last) * 99.0f / 4095.0f;
    int step = (int)extra_acc;
    extra_acc -= (float)step;
    if (hold)
        synth_set_env_hold(layer, cycle_env, synth_env_hold(layer, cycle_env) + step);
    else
        synth_set_env_delay(layer, cycle_env, synth_env_delay(layer, cycle_env) + step);
    if (pot != extra_pot) { // its name first, then the value
        extra_pot = pot;
        display_text(hold ? "HoLd" : "dLY");
        display_until = 0;
        extra_value_at = ms_ticks + 500;
    } else if (!extra_value_at)
        show_env_extra();
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
    if (kind == CONTROL_BUTTON && index == BUTTON_ARP_ASSIGN && !menu_open && !saving) {
        if (value)
            assign_press_at = ms_ticks ? ms_ticks : 1;
        else if (assign_press_at) { // released before the hold fired: a short press
            assign_press_at = 0;
            assign_short_press();
        }
        return;
    }
    if (kind == CONTROL_BUTTON && index == BUTTON_SYNC_SOURCE) {
        if (value) {
            shift_held = 1;
            if (ms_ticks - shift_tap_at < 400) { // two quick presses: latch
                shift_latched = !shift_latched;
                display_text(shift_latched ? "SHFt" : "oFF");
                display_until = ms_ticks + 800;
            }
            shift_tap_at = ms_ticks;
        } else
            shift_held = 0;
        shift_set(shift_active());
        return;
    }
    if (kind == CONTROL_BUTTON && index == BUTTON_REC && !value) {
        compare_end(); // (whether or not Shift is still down)
        rec_used = 0;
    }
    if (kind == CONTROL_BUTTON && value && recording
        && (index == BUTTON_REC || index == BUTTON_SEQ1 || index == BUTTON_SEQ2)) {
        if (index == BUTTON_SEQ2) {
            record_show(seq_record_rest());
            return;
        }
        rec_used = index == BUTTON_REC;
        record_end(1);
        return;
    }
    if (kind == CONTROL_BUTTON && index == BUTTON_SEQ1 && value && controls_button(BUTTON_REC) && !rec_used
        && !menu_open && !shift_active()) { // Rec + Seq 1: record steps (instead of saving; the edit layer stays)
        saving = 0;
        typed_digits = 0;
        seq_record_start();
        recording = 1;
        display_text("rSEq");
        display_until = 0;
        return;
    }
    if (kind == CONTROL_BUTTON && index == BUTTON_SYNC_CLOCK) {
        if (shift_active() && value) { // the clock source
            static const char names[3][5] = {"nIdI", "Int", "SYnC"};
            clock_set_source((clock_source + 1) % 3);
            display_text(names[clock_source]);
            display_until = ms_ticks + 1000;
        } else if (value)
            sync_press_at = ms_ticks ? ms_ticks : 1;
        else if (sync_press_at) { // released before the hold: a tap
            sync_press_at = 0;
            clock_tap();
        }
        return;
    }
    if (kind == CONTROL_SWITCH && index == SWITCH_SYNC_IN_A && value && clock_source == CLOCK_SYNC_IN)
        sync_in_pulse();
    if (shift_active() && kind == CONTROL_POT && !adsr_pot_index(index)) { // the ADSR knobs: Env 3 / 4, by the engine
        if (shift_pot_last[index] >= 0)
            shift_turn(index, (int)value - shift_pot_last[index]);
        shift_pot_last[index] = (int)value;
        return;
    }
    if (shift_active() && kind == CONTROL_BUTTON && value) {
        patch_t p;
        switch (index) {
        case BUTTON_REC: // compare: the stored patch while held
            if (preset_mode && !comparing && store_load_patch(current_patch, &p)) {
                synth_capture_patch(&compare_copy);
                synth_apply_patch(&p);
                comparing = 1;
                display_text("CoNP");
                display_until = 0;
            } else
                display_text("no");
            return;
        case BUTTON_PERF: // undo the last load or init
            if (undo_valid) {
                undo_swap_back();
                display_text("Undo");
            } else
                display_text("no");
            display_until = ms_ticks + 1000;
            return;
        case BUTTON_PRESET: // save to the current slot
            display_text(preset_mode && save_patch(current_patch) ? "donE" : "no");
            display_until = ms_ticks + 1000;
            return;
        case BUTTON_SEQ1: case BUTTON_SEQ2: { // copy the edit layer there
            int to = index == BUTTON_SEQ2;
            if (to != synth_edit_layer()) {
                synth_copy_layer(synth_edit_layer(), to);
                display_text("CoPY");
            } else
                display_text("SAnE");
            display_until = ms_ticks + 1000;
            return;
        }
        case BUTTON_0: // panic
            seq_stop();
            synth_all_off();
            sustain = 0;
            for (int n = 0; n < 128; n++)
                sustained[n] = 0;
            display_text("PAnC");
            display_until = ms_ticks + 1000;
            return;
        default:
            if (index >= BUTTON_1 && index <= BUTTON_9) { // straight to that Settings page
                menu_open = 1;
                menu_page = index - BUTTON_1;
                display_led(LED_SETTINGS, 1);
                display_until = 0;
                idle_display();
                return;
            }
        }
    }
    if (kind == CONTROL_POT && extra_frozen >> index & 1) { // the envelope page: delay and hold
        env_extra_turn(index, (int)value);
        return;
    }
    if (kind == CONTROL_POT && !assigning && !browsing) {
        last_pot = index;
        last_pot_at = ms_ticks;
    }
    if ((assigning || browsing) && kind == CONTROL_BUTTON && value
        && (index == BUTTON_PRESET || index == BUTTON_REC || index == BUTTON_SETTINGS || index == BUTTON_PERF))
        assign_end(); // and the button does its usual job
    if ((assigning || browsing) && assign_event(kind, index, value))
        return;
    if (kind == CONTROL_SWITCH && index == SWITCH_LFO_SHAPE)
        step_lfo_shape(1);
    if (kind == CONTROL_ENCODER && shape_until && (int32_t)(ms_ticks - shape_until) < 0) {
        shape_steps += (int)value;
        if (shape_steps >= 4 || shape_steps <= -4)
            step_lfo_shape(shape_steps > 0 ? 1 : -1);
        return;
    }
    if (kind == CONTROL_BUTTON && index == BUTTON_ARP_UP && value && menu_open
        && (menu_page == PAGE_CYCLE || menu_page == PAGE_LAYERS)) {
        // the envelope page: its mode, trigger, timing, curve, count; the layer page: the mode or the voice share
        alt_page = menu_page == PAGE_CYCLE ? (alt_page + 1) % ENV_PAGES : !alt_page;
        display_text(page_name(menu_page));
        display_until = 0;
        show_value_at = ms_ticks + 700;
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
        static const char names[3][5] = {"OFF", "HALF", "FULL"};
        display_text(names[controls_keytrack()]); // keyboard tracking
        display_until = ms_ticks + 1000;
    }
    if (kind == CONTROL_POT) {
        // a per-layer knob still in pickup shows the edit layer's value, with a dot
        int pending = synth_pot_pending(index);
        float knob = pending ? synth_pot_value(index) : synth_knob_snap(index, (float)value / 4095.0f);
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
        } else if (env_knob(index, &env, &stage) && synth_env_tempo_step(env, stage, knob) >= 0) {
            const char *name = synth_env_note_name(synth_env_tempo_step(env, stage, knob)); // a note value
            int n = 0;
            while (name[n] && n < 4) {
                text[n] = name[n];
                n++;
            }
            text[n] = 0;
        } else if (env_knob_time(index, knob) >= 0) {
            format_time(env_knob_time(index, knob), text);
        } else if (index == POT_FREQ_A || index == POT_FREQ_B) {
            format_signed((float)synth_knob_semitones(knob), text);
        } else if (index == POT_FINE) {
            format_signed((float)synth_knob_cents(knob), text);
        } else if (index == POT_MASTER_TUNE) { // Macro 1
            float m = synth_knob_macro(knob) * 99.0f;
            format_signed((float)(int)(m < 0 ? m - 0.5f : m + 0.5f), text);
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
        if (page != menu_page)
            alt_page = 0;
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
        if (typed_digits && ms_ticks - typed_at < 3000 && typed_value * 10 + digit <= PATCH_COUNT
            && (typed_digits == 2 || (typed_value * 10 + digit) * 10 > PATCH_COUNT || (typed_value * 10 + digit) == 0)) {
            // the number is complete: a third digit, or no further digit could keep it within 1-128
            int n = typed_value * 10 + digit - 1;
            typed_digits = 0;
            if (n < 0)
                n = current_patch;
            if (saving) {
                current_patch = n;
                show_patch(n);
            } else
                load_patch(n);
        } else if (typed_digits && ms_ticks - typed_at < 3000 && typed_value * 10 + digit <= PATCH_COUNT) {
            typed_digits = 2; // "P 12-": one more digit may follow (within 3 s), or VALUE / a key ends it
            typed_value = typed_value * 10 + digit;
            typed_at = ms_ticks;
            char text[6] = {'P', ' ', (char)('0' + typed_value / 10), (char)('0' + typed_value % 10), 0};
            display_text(text);
            display_until = 0;
        } else {
            typed_digits = 1;
            typed_value = digit;
            typed_at = ms_ticks;
            char text[6] = {'P', ' ', ' ', (char)('0' + digit), 0};
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
                current_patch = n; // a damaged slot: show it, keep the sound
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
    SET_LFO1_DELAY, SET_LFO1_FADE, SET_LFO2_DELAY, SET_LFO2_FADE, // 0-99 (lfo.h)
    SET_CC_A, SET_CC_B,                                  // the CC A / B mod sources' controller numbers (1-127)
    SET_MACRO_L1_1, SET_MACRO_L1_2, SET_MACRO_L2_1, SET_MACRO_L2_2, // the macros' offsets + 99 (0-198)
    SET_CLOCK_SOURCE, SET_BPM,                           // 0 MIDI, 1 internal, 2 SYNC IN; 30-300 (system-wide)
    SET_ENV_TRIGGER_L1, SET_ENV_TRIGGER_L2, SET_ENV_DAHDSR_L1, SET_ENV_DAHDSR_L2, // a bit per envelope (0.9.0)
    SET_ENV_CURVE_L1, SET_ENV_CURVE_L2,                  // 2 bits per envelope, ENV_CURVE_*
    SET_ENV_DELAY, // 8 of them, then 8 holds: layer 1 amp, filter, Env 3, Env 4, then layer 2 (0-99)
    SET_ENV_HOLD = SET_ENV_DELAY + 8,
    SET_LAYER_VOICES = SET_ENV_HOLD + 8,                 // layer 1's voices in the two-layer modes, 1-7; 0 automatic
    SET_ENV_COUNT, // 8 of them, as the delays: cycles, 0 endless
    SET_ENV_COUNT_END = SET_ENV_COUNT + 7,
    SET_ENV_TEMPO_L1, SET_ENV_TEMPO_L2, // a bit per envelope: stage times in note values
    SET_MIDI_CHANNEL,                                    // 1-16, 0 every channel (system-wide)
    SET_WRITABLE,                                        // read-only from here
    SET_ENV_SELECT = SET_WRITABLE, SET_PRESET_MODE, SET_CURRENT_PATCH, SET_MORPH, SET_COUNT // morph: set with 44
} host_setting_t;
_Static_assert(8 + 2 + 2 * SET_COUNT + 1 <= SYSEX_MAX, "the settings reply fits");

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
    case SET_LFO1_DELAY: case SET_LFO2_DELAY: return lfo_delay[id == SET_LFO2_DELAY];
    case SET_LFO1_FADE: case SET_LFO2_FADE: return lfo_fade[id == SET_LFO2_FADE];
    case SET_CC_A: case SET_CC_B: return synth_cc_number(id == SET_CC_B);
    case SET_MACRO_L1_1: case SET_MACRO_L1_2: case SET_MACRO_L2_1: case SET_MACRO_L2_2:
        return synth_macro_offset((id - SET_MACRO_L1_1) / 2, (id - SET_MACRO_L1_1) % 2) + 99;
    case SET_CLOCK_SOURCE: return clock_source;
    case SET_BPM: return (int)(clock_bpm + 0.5f);
    case SET_ENV_TRIGGER_L1: case SET_ENV_TRIGGER_L2: return synth_env_trigger(id - SET_ENV_TRIGGER_L1);
    case SET_ENV_DAHDSR_L1: case SET_ENV_DAHDSR_L2: return synth_env_dahdsr(id - SET_ENV_DAHDSR_L1);
    case SET_ENV_CURVE_L1: case SET_ENV_CURVE_L2: return synth_env_curve(id - SET_ENV_CURVE_L1);
    case SET_ENV_DELAY ... SET_ENV_DELAY + 7: return synth_env_delay((id - SET_ENV_DELAY) / 4, (id - SET_ENV_DELAY) % 4);
    case SET_ENV_HOLD ... SET_ENV_HOLD + 7: return synth_env_hold((id - SET_ENV_HOLD) / 4, (id - SET_ENV_HOLD) % 4);
    case SET_LAYER_VOICES: return synth_layer_voices();
    case SET_ENV_COUNT ... SET_ENV_COUNT_END: return synth_env_count((id - SET_ENV_COUNT) / 4, (id - SET_ENV_COUNT) % 4);
    case SET_ENV_TEMPO_L1: case SET_ENV_TEMPO_L2: return synth_env_tempo(id - SET_ENV_TEMPO_L1);
    case SET_MIDI_CHANNEL: return midi_channel;
    case SET_MORPH: return synth_morph_target();
    case SET_ENV_SELECT: return synth_env_select();
    case SET_PRESET_MODE: return preset_mode;
    case SET_CURRENT_PATCH: return current_patch;
    default: return 0;
    }
}

static int host_setting_set(int id, int value)
{
    static const uint16_t counts[SET_WRITABLE] = {
        LFO_SYNC_COUNT, LFO_TRIGGER_COUNT, LAYER_MODE_COUNT, 128, 3, LFO_SYNC_COUNT, LFO_TRIGGER_COUNT, 2, 2,
        LFO_SHAPE_COUNT, LFO_SHAPE_COUNT, LFO_SHAPE_COUNT, LFO_SHAPE_COUNT, 8, 8, 16, 16,
        [SET_CYCLE_SYNC ... SET_CYCLE_SYNC + 7] = LFO_DIVISIONS + 1, [SET_BEND] = 13, [SET_DETUNE] = 51,
        [SET_VOICE_L1] = 11, [SET_VOICE_L2] = 11, [SET_DRIFT] = 100, [SET_SCALE] = SCALE_COUNT, [SET_ROOT] = 12,
        [SET_LFO1_DELAY ... SET_LFO2_FADE] = 100, [SET_CC_A] = 128, [SET_CC_B] = 128,
        [SET_MACRO_L1_1 ... SET_MACRO_L2_2] = 199, [SET_CLOCK_SOURCE] = 3, [SET_BPM] = 301,
        [SET_ENV_TRIGGER_L1 ... SET_ENV_DAHDSR_L2] = 16, [SET_ENV_CURVE_L1] = 256, [SET_ENV_CURVE_L2] = 256,
        [SET_ENV_DELAY ... SET_ENV_HOLD + 7] = 100, [SET_LAYER_VOICES] = VOICE_COUNT,
        [SET_ENV_COUNT ... SET_ENV_COUNT_END] = 100, [SET_ENV_TEMPO_L1] = 16, [SET_ENV_TEMPO_L2] = 16,
        [SET_MIDI_CHANNEL] = 17,
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
    case SET_LFO1_DELAY: case SET_LFO2_DELAY: lfo_delay[id == SET_LFO2_DELAY] = (uint8_t)value; break;
    case SET_LFO1_FADE: case SET_LFO2_FADE: lfo_fade[id == SET_LFO2_FADE] = (uint8_t)value; break;
    case SET_CC_A: case SET_CC_B:
        if (value < 1)
            return 0;
        synth_set_cc_number(id == SET_CC_B, value);
        store_set_cc_number(id == SET_CC_B, value);
        break;
    case SET_MACRO_L1_1: case SET_MACRO_L1_2: case SET_MACRO_L2_1: case SET_MACRO_L2_2:
        synth_set_macro_offset((id - SET_MACRO_L1_1) / 2, (id - SET_MACRO_L1_1) % 2, value - 99);
        break;
    case SET_CLOCK_SOURCE: clock_set_source(value); break;
    case SET_BPM:
        if (value < 30)
            return 0;
        clock_set_bpm((float)value);
        break;
    case SET_ENV_TRIGGER_L1: case SET_ENV_TRIGGER_L2: synth_set_env_trigger(id - SET_ENV_TRIGGER_L1, value); break;
    case SET_ENV_DAHDSR_L1: case SET_ENV_DAHDSR_L2: synth_set_env_dahdsr(id - SET_ENV_DAHDSR_L1, value); break;
    case SET_ENV_CURVE_L1: case SET_ENV_CURVE_L2: synth_set_env_curve(id - SET_ENV_CURVE_L1, value); break;
    case SET_ENV_DELAY ... SET_ENV_DELAY + 7:
        synth_set_env_delay((id - SET_ENV_DELAY) / 4, (id - SET_ENV_DELAY) % 4, value);
        break;
    case SET_ENV_HOLD ... SET_ENV_HOLD + 7:
        synth_set_env_hold((id - SET_ENV_HOLD) / 4, (id - SET_ENV_HOLD) % 4, value);
        break;
    case SET_LAYER_VOICES: synth_set_layer_voices(value); break;
    case SET_ENV_COUNT ... SET_ENV_COUNT_END:
        synth_set_env_count((id - SET_ENV_COUNT) / 4, (id - SET_ENV_COUNT) % 4, value);
        break;
    case SET_ENV_TEMPO_L1: case SET_ENV_TEMPO_L2: synth_set_env_tempo(id - SET_ENV_TEMPO_L1, value); break;
    case SET_MIDI_CHANNEL: set_midi_channel(value); break;
    default:
        synth_set_lfo_shape((id - SET_SHAPE_L1_LFO1) / 2, (id - SET_SHAPE_L1_LFO1) % 2, value);
        break;
    }
    show_edit_layer();
    return 1;
}


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
    if (data_length == 1 && data[0] == 0x55) {
        reply[n++] = 0x55;
        n += synth_probe(&reply[n]);
    }
    if (data_length == 11 && data[0] == 0x6C) {
        dac_config_t c = {.bus = data[1], .mode = data[2], .high = (uint16_t)(data[3] << 7 | data[4]),
                          .low = (uint16_t)(data[5] << 7 | data[6]), .speed = data[7], .sync_us = data[8]};
        sh_hold();
        int ok = dac_configure(&c);
        if (ok)
            sh_set_timing(data[9], data[10]);
        reply[n++] = 0x7F;
        reply[n++] = 0x6C;
        reply[n++] = (uint8_t)ok;
    }
    if (data_length == 2 && data[0] == 0x6E) {
        sh_dac_setup_resend(data[1] != 0);
        reply[n++] = 0x7F;
        reply[n++] = 0x6E;
    }
    if (data_length == 5 && data[0] == 0x6F) {
        sh_dac_register(data[1], (uint16_t)(data[2] << 14 | data[3] << 7 | data[4]));
        reply[n++] = 0x7F;
        reply[n++] = 0x6F;
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
    if ((data_length == 3 || data_length == 4) && data[0] == 0x7D
        && host_setting_set(data[1], data_length == 4 ? data[2] << 7 | data[3] : data[2])) { // 4 bytes: 14-bit value
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
    if (data_length == 1 && data[0] == 0x5B) { // format: every slot empty, the EEPROM marked as ours
        synth_all_off();
        int ok = store_format();
        reply[n++] = 0x5B;
        reply[n++] = 0;
        reply[n++] = (uint8_t)PATCH_COUNT;
        reply[n++] = (uint8_t)!ok;
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
                                                         cycles_controls, cycles_display, passes_per_second * mhz,
                                                         cycles_ticks};
        reply[n++] = 0x58;
        for (int i = 0; i < 8; i++) {
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
    if (data_length == 4 && data[0] == 0x78 && data[1] < VOICE_COUNT && data[2] < 3) {
        static const uint8_t rows[] = {TUNING_ROW_OSC_A, TUNING_ROW_OSC_B, TUNING_ROW_FILTER};
        static const uint8_t cvs[] = {CV_OSC_A1, CV_OSC_B1, CV_FIL1};
        sh_set((cv_t)(cvs[data[2]] + data[1]), tuning_note_cv(rows[data[2]] + data[1], data[3]));
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
// Also CC 64 sustain pedal (bank select is ignored: one bank).
static const struct { uint8_t cc, param; } cc_map[] = {
    {14, POT_FREQ_A}, {15, POT_FREQ_B}, {16, POT_FINE}, {17, POT_A_PW}, {18, POT_B_PW}, {19, POT_A_LEVEL},
    {20, POT_B_LEVEL}, {21, POT_NOISE}, {74, POT_CUTOFF}, {71, POT_RESONANCE}, {22, POT_FIL_ENV_AMT},
    {23, POT_FIL_ATT}, {24, POT_FIL_DEC}, {25, POT_FIL_SUS}, {26, POT_FIL_REL}, {73, POT_AMP_ATT}, {75, POT_AMP_DEC},
    {70, POT_AMP_SUS}, {72, POT_AMP_REL}, {5, POT_GLIDE}, {76, POT_LFO_FREQ}, {77, POT_LFO_AMOUNT},
    {27, PARAM_LFO2_RATE}, {28, PARAM_LFO2_AMOUNT}, {29, POT_PMOD_FIL_ENV}, {30, POT_PMOD_OSC_B},
    {31, POT_MASTER_TUNE}, {85, PARAM_ENV3_ATT}, {86, PARAM_ENV3_DEC}, {87, PARAM_ENV3_SUS}, {88, PARAM_ENV3_REL},
    {89, PARAM_ENV4_ATT}, {90, PARAM_ENV4_DEC}, {102, PARAM_ENV4_SUS}, {103, PARAM_ENV4_REL},
};
// A note let go on `layers` (the pedal's release): the sequence's key, else the engine's
static void release_key(uint8_t note, int layers)
{
    if (!layers)
        return;
    int rest = layers & ~seq_key_off(note, layers);
    if (rest)
        synth_note_off_layers(note, rest);
}

// A key let go on a MIDI channel: the pedal holds it on the layers the pedal is down for; in the per-channel modes
// it releases only its channel's voice
static void key_off(uint8_t note, int channel)
{
    int layers = synth_channel_layers(channel), held = layers & sustain;
    sustained[note & 0x7F] |= (uint8_t)held;
    layers &= ~held;
    int pv = synth_channel_voice(channel);
    if (!layers)
        return;
    if (pv >= 0)
        synth_voice_note_off(pv, note);
    else
        release_key(note, layers);
}

static void control_change(uint8_t cc, uint8_t value, int channel)
{
    if (cc == 64) { // sustain pedal, for the layers the channel plays
        int layers = synth_channel_layers(channel);
        synth_set_pedal(layers, value >= 64);
        if (value >= 64) {
            sustain |= layers;
            return;
        }
        sustain &= ~layers;
        for (int n = 0; n < 128; n++)
            if (sustained[n] & layers) {
                int off = sustained[n] & layers;
                sustained[n] &= (uint8_t)~off;
                release_key((uint8_t)n, off);
            }
        return;
    }
    if (cc == 74) { // timbre: the channel's voice in the per-channel modes, else the layers it plays
        int pv = synth_channel_voice(channel);
        if (pv >= 0)
            synth_voice_timbre(pv, value);
        else
            synth_timbre(value, synth_channel_layers(channel));
        return;
    }
    if (cc == 0 || cc == 32) // bank select: one bank
        return;
    if (cc == synth_cc_number(0) || cc == synth_cc_number(1)) {
        synth_cc_source(cc == synth_cc_number(0) ? 0 : 1, value, synth_channel_layers(channel));
        if (cc != 1 && cc != 74)
            return;
    }
    for (unsigned i = 0; i < sizeof cc_map / sizeof cc_map[0]; i++)
        if (cc_map[i].cc == cc) {
            int layer = synth_layer_mode() == LAYER_CHANNEL ? channel == 1 : synth_edit_layer();
            synth_set_param(layer, cc_map[i].param, (float)value / 127.0f);
            return;
        }
}

static void handle_midi(void);

// The sequence advances by the time since it last did: once at the start of a pass and before each tick (bounded,
// so a stall such as tuning doesn't run it through many steps at once).
static void advance_sequence(void)
{
    static uint32_t last;
    uint32_t now = DWT_CYCCNT;
    float dt = (float)(now - last) / (float)system_hz;
    last = now;
    seq_update(dt > 0.05f ? 0.05f : dt);
}

// The CV refresh with the fast ticks run in between (synth_tick(): envelopes, LFOs and the routes from them), so
// those move more often than the engine's pass. Before each tick MIDI is read and the sequence advanced, so a note
// starts within a tick rather than a pass: a voice that just started gets its pass terms and its pitch at once, so
// its VCA never opens on the old note. Each tick writes two voices' pitch, every other tick the cutoffs, and every
// tick the VCAs (alongside the DAC80501 channels at their address, the rest on their own); the global CVs go once a
// pass, mostly alongside too. Silent voices' pitch and cutoff only have to be held: one of them is refreshed each
// pass, in turn, so no pass is much longer than the others.
#define TICKS 4
static void refresh_cvs(void)
{
    static uint32_t last_tick, count;
    unsigned fresh = synth_take_fresh_voices();
    unsigned voices = (synth_active_voices() & ~fresh) | 1u << (count++ % VOICE_COUNT);
    sh_refresh_pitch(fresh);
    cycles_ticks = 0;
    for (int k = 0; k < TICKS; k++) {
        uint32_t now = DWT_CYCCNT;
        float dt = (float)(now - last_tick) / (float)system_hz;
        last_tick = now;
        if (k) { // (the pass began with these)
            handle_midi();
            clock_run();
            advance_sequence();
            unsigned started = synth_take_fresh_voices();
            if (started) {
                synth_prepare_voices(started);
                sh_refresh_pitch(started);
                fresh |= started;
            }
        }
        int cutoff = !(k & 1);
        unsigned pitch = voices & ~fresh & 3u << 2 * k; // two voices' pitch per tick
        now = DWT_CYCCNT;
        synth_tick(dt, cutoff, pitch);
        cycles_ticks += DWT_CYCCNT - now;
        sh_refresh_pitch(pitch);
        if (cutoff)
            sh_refresh_cutoff(voices | fresh);
        sh_refresh_vca((cutoff ? voices | fresh : 0) | pitch);
    }
    sh_refresh_global();
}

// MIDI realtime and Song Position Pointer, straight from the USB and DIN interrupts (usb_midi_realtime(), din_init()):
// the clock the LFOs and the sequence follow
static void realtime(uint8_t status, uint16_t data, uint32_t cycles)
{
    if (clock_source != CLOCK_MIDI)
        return; // the generator owns the clock
    if (status == 0xF8)
        lfo_clock_tick(cycles);
    else if (status == 0xFA)
        lfo_clock_start();
    else if (status == 0xFB)
        lfo_clock_continue();
    else if (status == 0xFC)
        lfo_clock_stop();
    else if (status == 0xF2)
        lfo_clock_song_position(data); // for the next Continue
}

// A channel message's channel counted from the MIDI channel setting, or -1 if the synth isn't listening to it. Set
// to every channel, it's the channel as it comes; set to N, it's N (0 from here on) and, in the layer modes that
// play by channel, the ones after it: channel mode N and N + 1, a voice per channel N to N + 7, MPE N (the master)
// to N + 8.
static int listen_channel(uint8_t status)
{
    int channel = status & 0x0F;
    if (!midi_channel)
        return channel;
    channel -= midi_channel - 1;
    int span;
    switch (synth_layer_mode()) {
    case LAYER_CHANNEL: span = 2; break;
    case LAYER_VOICE_PER_CHANNEL: span = VOICE_COUNT; break;
    case LAYER_MPE: span = VOICE_COUNT + 1; break;
    default: span = 1; break;
    }
    return channel >= 0 && channel < span ? channel : -1;
}

static void handle_midi(void)
{
    static uint8_t sysex[SYSEX_MAX];
    static int sysex_length;
    uint8_t p[4];

    while (usb_midi_read(p) || din_midi_read(p)) { // USB first, then the 5-pin socket
        uint8_t cin = p[0] & 0x0F;
        uint8_t status = p[1] & 0xF0;
        if (cin >= 0x8 && cin <= 0xE) { // a channel message: from here on its channel counts from the setting
            int channel = listen_channel(p[1]);
            if (channel < 0)
                continue;
            p[1] = (uint8_t)(status | channel);
        }
        switch (cin) {
        case 0x9: {
            if (direct_mode)
                break;
            int channel = p[1] & 0x0F, pv = synth_channel_voice(channel);
            if (p[3] && menu_open && menu_page == PAGE_SPLIT) { // play a note to set the split point
                synth_set_split_note(p[2]);
                show_setting(PAGE_SPLIT);
            } else if (!synth_channel_plays(channel)) {
                break;
            } else if (p[3]) { // the sequence takes the key if it's on for that key's layer
                sustained[p[2] & 0x7F] &= (uint8_t)~synth_channel_layers(channel);
                if (recording) // the step is written; the note plays without the sequence
                    record_show(seq_record_note(p[2], p[3]));
                if (pv >= 0)
                    synth_voice_note_on(pv, p[2], p[3]);
                else {
                    int layers = synth_key_layers(p[2], channel);
                    int rest = layers & ~(recording ? 0 : seq_key_on(p[2], p[3], layers));
                    if (rest)
                        synth_note_on_layers(p[2], p[3], rest);
                }
            } else
                key_off(p[2], channel);
            break;
        }
        case 0x8:
            key_off(p[2], p[1] & 0x0F);
            break;
        case 0xC: // program change 0-127: patch 1-128
            if (p[2] < PATCH_COUNT)
                load_patch(p[2]);
            break;
        case 0xB:
            if (status == 0xB0 && (p[2] == 120 || p[2] == 123)) { // all sound off, all notes off
                seq_stop();
                synth_all_off();
            }
            if (status == 0xB0 && p[2] == 1)
                synth_mod_wheel(p[3], synth_channel_layers(p[1] & 0x0F));
            if (status == 0xB0)
                control_change(p[2], p[3], p[1] & 0x0F);
            break;
        case 0xE: {
            int channel = p[1] & 0x0F, pv = synth_channel_voice(channel);
            if (pv >= 0)
                synth_voice_bend(pv, p[2] | p[3] << 7);
            else
                synth_pitch_bend(p[2] | p[3] << 7, synth_channel_layers(channel));
            break;
        }
        case 0xD: { // channel pressure
            int channel = p[1] & 0x0F, pv = synth_channel_voice(channel);
            if (pv >= 0)
                synth_voice_pressure(pv, p[2]);
            else
                synth_aftertouch(p[2], synth_channel_layers(channel));
            break;
        }
        case 0xA: // poly pressure: the voices playing that note
            synth_poly_pressure(p[2], p[3], synth_channel_layers(p[1] & 0x0F));
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

    for (int k = 0; k < 2; k++)
        if (store_cc_number(k))
            synth_set_cc_number(k, store_cc_number(k));
    midi_channel = store_midi_channel();
    clock_source = store_clock_source();
    clock_bpm = (float)store_bpm_tenths() / 10.0f;
    clock_period = (uint32_t)((float)system_hz * 60.0f / (clock_bpm * 24.0f));
    if (store_formatted()) {
        display_text("ForM"); // an older layout of ours was wiped: every slot is empty now
        display_update();
    }
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
            clock_run();
            advance_sequence();
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
        if (saving || recording)
            display_led(LED_REC, (now / (recording ? 125 : 250)) & 1); // faster while recording steps
        display_led(LED_SYNC_CLOCK, lfo_beat_phase() < 0.12f); // the beat, from whichever clock
        // signed: the press is stamped from ms_ticks during the poll, which can be a tick past `now`, and unsigned
        // that wrapped into a hold at once (a tap of Arp Assign opened Browse, a tap of Sync Clock restarted the bar)
        if (sync_press_at && (int32_t)(now - sync_press_at) >= 600) {
            sync_press_at = 0;
            clock_restart();
            display_text("StAr");
            display_until = now + 600;
        }
        uint32_t extra = menu_open && menu_page == PAGE_CYCLE ? ENV_EXTRA_POTS : 0;
        if (extra != extra_frozen) { // the envelope page opened or closed: ATT / DEC set delay / hold meanwhile
            extra_frozen = extra;
            synth_freeze_pots(extra);
            extra_pot = -1;
            for (int i = 0; i < POT_COUNT; i++)
                extra_last[i] = -1;
        }
        if (extra_value_at && (int32_t)(now - extra_value_at) > 0) {
            extra_value_at = 0;
            show_env_extra();
        }
        if (shift_value_at && (int32_t)(now - shift_value_at) > 0) {
            shift_value_at = 0;
            show_shift_value();
            display_until = now + 1500;
        }
        if (assign_press_at && (int32_t)(now - assign_press_at) >= 600) {
            assign_press_at = 0;
            assign_hold();
        }
        if (assigning)
            display_led(LED_ARP_ASSIGN, (now / 250) & 1);
        if (browsing && !display_until && browse_frame_now() != browse_frame)
            browse_show();
        tc = DWT_CYCCNT;
        if (!report_mode)
            display_update();
        cycles_display = DWT_CYCCNT - tc;

        t0 = DWT_CYCCNT;
        if (direct_mode)
            sh_refresh();
        else
            refresh_cvs();
        if (passes % 128 == 0) // about every tenth of a second
            sh_refresh_dac_setup();
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
