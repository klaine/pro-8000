// Patch EEPROM layout (128 kB, from 0.8.0 / format 2; the stock image in backup/ is the way back to stock firmware):
//   0x00000-0x17FFF  128 patch records of 768 bytes, each a patch and its step sequence
//   0x19000-0x1F3FF  the stock factory bank (100 patches of 256 bytes), left as it is, for importing
//   0x1F400          global block: format, last patch, the CC A / B numbers, the MIDI channel
//   0x1F500-0x1FBFF  the autotune tables (tuning.c)
//
// Patch record (768 bytes, little-endian), two halves with their own checksums so the sequence can be written alone:
//   0   "P8P2"
//   4   name[16]                  0-padded
//   20  layer mode, split note, LFO sync[2], LFO retrigger[2], LFO delay[2], LFO fade[2]
//   30  keyboard tracking, drift, morph target + 1 (0 = none), scale | root << 4, voice modes, bend range, detune + 1
//   37  layer 1's voices in the two-layer modes (1-7, 0 = automatic; 0.9.0), 38 the oscillators' tuning modes[2]
//       (per layer, so 38-39: A bits 0-1, B bits 2-3; 0 = semitones, as before; 0.9.2)
//   40  switches (u16)            bit s = switch s engaged
//   42  LFO 2 destinations[2], 44 cycling envelopes[2], 46 step counts[2][2], 50 step patterns[2][2] (u16),
//   58  synced cycling[2][4] (0 free, else clock division + 1), 66 LFO shapes[2][2], 70 macro offsets[2][2]
//       (int8, 0.8.1), 74 envelope key-up triggers[2], 76 DAHDSR envelopes[2] (a bit per envelope), 78 envelope
//       curves[2] (2 bits per envelope), 80 envelope delays[2][4], 88 holds[2][4] (0-99; all 0.9.0)
//   96  pots[2][26] (u16)         layer 1, then layer 2
//   200 extra[2][10] (u16)        Env 3 and Env 4 ADSR, LFO 2 rate and amount, per layer
//   240 routes[2][16]             source, destination, depth (int8) per layer
//   336 cycle counts[2][4] (0 = endless), 344 1 if the key-up triggers apply to cycling envelopes too (both 0.9.1;
//       0: a record from 0.9.0 or earlier, where cycling won, so its cycling envelopes' key-up bits are cleared at load)
//   345 stage times in note values[2] (a bit per envelope; 0.9.1)
//   347 layer links (LINK_* bits: what layer 2 takes from layer 1; 0.9.3), 348 one shot cycles[2] (per LFO, 0 = 1; 0.9.3),
//   350 1 if a sequence's keys restart the LFOs, 0 its steps (0.9.3), 351 both-layers routes[2] (u16, bit r: route r plays
//       on the other layer too; 0.9.3), 355 unison voices (2-8, 0 = 8; 0.9.5), 356 route Vias[2][16] (u16, mod.h; 0.9.5), 420 route depths' tenths[2][16] (int8, mod.h; 0.9.5), 452 arp range, 453 arp extension, 454 arp chaos (seq.h; 0.9.5), 455 chords[2][8] (per layer
//       up to 7 intervals, 0 ending them, and a flags byte; synth.h; 0.10.0), 471 reserved to 508
//   508 checksum (u32) over 0-507
//   512 "P8S2", 516 length, lanes, division, bpm, flags (bits 5-6: chord lanes, 0.9.3), swing, smooth, arp, then from 524 each step's note,
//       velocity, gate, extra (length × 4 bytes) and each lane's values (length bytes each); 764 checksum over
//       512-763
// Zeros in a newer field always mean what older firmware did. A record whose first half isn't ours is an empty
// slot (all zero) or corrupt. Format 1 (0.7.x: 256-byte patches at 0, sequences from 0x0C800) is wiped at the
// first start of a format-2 firmware; the EEPROM backups keep those patches.
// Stock patches start with a5 16 61 00 and the format byte 0x6D or 0x6F; their fields are documented in
// samstaton/pro800 (pro800syx.md) and converted here field for field. The value curves of our engine differ from
// the stock firmware's, so an imported sound is close rather than identical.
#include "store.h"
#include "eeprom.h"
#include "seq.h"

#define RECORD_SIZE   768u
#define PATCH_AREA    (PATCH_COUNT * RECORD_SIZE) // 0x18000
#define STOCK_SIZE    256u
#define GLOBAL_ADDRESS 0x1F400u
#define PATCH_MAGIC   0x32503850u // "P8P2"
#define SEQ_MAGIC     0x32533850u // "P8S2"
#define GLOBAL_MAGIC  0x31473850u // "P8G1"
#define FORMAT        2
#define FACTORY_ADDRESS 0x19000u

#define OFF_NAME 4
#define OFF_MODE 20
#define OFF_SPLIT 21
#define OFF_LFO_SYNC 22
#define OFF_LFO_TRIGGER 24
#define OFF_LFO_DELAY 26
#define OFF_LFO_FADE 28
#define OFF_KEYTRACK 30
#define OFF_DRIFT 31
#define OFF_MORPH 32
#define OFF_SCALE 33
#define OFF_VOICE_MODE 34
#define OFF_BEND 35
#define OFF_DETUNE 36
#define OFF_LAYER_VOICES 37
#define OFF_OSC_TUNE 38 // 38-39
#define OFF_SWITCHES 40
#define OFF_LFO2_DESTS 42
#define OFF_CYCLE 44
#define OFF_STEP_COUNTS 46
#define OFF_STEPS 50
#define OFF_ENV_SYNC 58
#define OFF_SHAPES 66
#define OFF_MACRO 70 // [layer][macro] offsets, int8
#define OFF_ENV_TRIGGER 74
#define OFF_ENV_DAHDSR 76
#define OFF_ENV_CURVE 78
#define OFF_ENV_DELAY 80 // [layer][envelope]
#define OFF_ENV_HOLD 88
#define OFF_POTS 96
#define OFF_EXTRA 200
#define OFF_ROUTES 240
#define OFF_ENV_COUNT 336
#define OFF_UP_CYCLES 344
#define OFF_ENV_TEMPO 345
#define OFF_LINK 347
#define OFF_LFO_CYCLES 348 // 348-349
#define OFF_LFO_RESTART 350
#define OFF_ROUTE_BOTH 351 // 351-354
#define OFF_UNISON_VOICES 355
#define OFF_ARP_RANGE 452
#define OFF_ARP_EXTEND 453
#define OFF_ARP_CHAOS 454
#define OFF_ROUTE_VIA 356 // 356-419
#define OFF_ROUTE_FINE 420 // 420-451
#define OFF_CHORD 455      // 455-470
#define OFF_RESERVED 471   // 471-507: free (37 B), written 0; a new field takes its bytes from here (NOTES: 0.10.0 plan)
#define OFF_CHECKSUM 508
#define SEQ_OFF 512
#define SEQ_HEADER 4
#define SEQ_DATA 12
#define SEQ_CHECKSUM 252
#define SEQ_SIZE 256u

static struct {
    int valid;
    uint16_t last_patch;
    uint8_t format, cc[2], clock_source, midi_channel, no_pickup;
    uint16_t bpm; // ×10
    uint8_t init_slot; // 0.10.0: Perf's init patch, slot + 1 (0 the built-in sound)
} global;

static uint32_t get32(const uint8_t *b) { return (uint32_t)b[0] | (uint32_t)b[1] << 8 | (uint32_t)b[2] << 16 | (uint32_t)b[3] << 24; }
static uint16_t get16(const uint8_t *b) { return (uint16_t)(b[0] | b[1] << 8); }
static void put32(uint8_t *b, uint32_t v) { b[0] = (uint8_t)v; b[1] = (uint8_t)(v >> 8); b[2] = (uint8_t)(v >> 16); b[3] = (uint8_t)(v >> 24); }
static void put16(uint8_t *b, uint16_t v) { b[0] = (uint8_t)v; b[1] = (uint8_t)(v >> 8); }

static uint32_t checksum(const uint8_t *b, uint32_t n)
{
    uint32_t sum = 0x12345678u;
    for (uint32_t i = 0; i < n; i++)
        sum = (sum << 5 | sum >> 27) ^ b[i];
    return sum;
}

static int is_stock(const uint8_t *b) { return b[0] == 0xA5 && b[1] == 0x16 && b[2] == 0x61 && b[3] == 0x00; }

// The settings kept here change on every step of a knob sweep and on every tap, and a write holds the engine and the CV
// refresh for up to 5 ms. So their setters only mark the block, and store_poll() writes it once they have rested. RAM
// stays the truth meanwhile; a power cut inside the rest loses the change. A chip that doesn't answer holds them for tens
// of ms a try (eeprom.c's timeout, and a status read each 50 us), so after GLOBAL_TRIES failures it's left alone until the
// next change.
#define GLOBAL_REST_MS 1000u
#define GLOBAL_TRIES 3
static int global_dirty, global_changed, global_failures;
static uint32_t global_changed_at;

static void global_mark(void)
{
    global_dirty = global_changed = 1;
    global_failures = 0;
}

// The global block's bytes. 0-15 from 0.7 (checksum at 12 over 0-11), 16-18 the clock (0.9.0, outside it, range-checked).
// From 0.10.0 (NOTES: the 0.10.0 plan's allocation table): 19 the init patch (slot + 1), 20-27 for MIDI out (20 the socket,
// 21 notes out, 22 out channel, 23 flags, 24-25 CC out numbers, 26-27 spare), 28-31 a checksum over 16-27. Bytes 20-27
// are written 0 until they're used, under a valid checksum, so a 0 there must always mean that field's default (CC out
// 0: its default number, as CC A / B's 0 does). 0.9.5 writes only 0-19, so a block it wrote (any setting changed, a
// format, another last patch) and older leftovers fail the checksum and read as the defaults; a downgrade that never
// wrote the block leaves 0.10.0's bytes as they were, which is harmless: they still mean the same patches.
#define GLOBAL_SIZE 32
#define GLOBAL_EXT 16 // the second checksum's start

static int write_global(void)
{
    uint8_t b[GLOBAL_SIZE] = {0};
    put32(b, GLOBAL_MAGIC);
    put16(b + 4, global.last_patch);
    b[6] = 1; // preset mode until 0.9.3, whose boot read it; 0.9.4 always boots on the last patch
    b[7] = FORMAT;
    b[8] = global.cc[0];
    b[9] = global.cc[1];
    b[10] = global.midi_channel; // 0.9.1; 0 in older blocks: every channel, as before
    b[11] = global.no_pickup;    // 0.9.3; 0 in older blocks: knobs pick up, as before
    put32(b + 12, checksum(b, 12));
    put16(b + 16, global.bpm); // the clock, outside the checksum (older blocks have anything here: range-checked)
    b[18] = global.clock_source;
    b[19] = global.init_slot;
    put32(b + 28, checksum(b + GLOBAL_EXT, 28 - GLOBAL_EXT));
    if (!eeprom_write(GLOBAL_ADDRESS, b, sizeof b)) {
        global_failures++;
        return 0;
    }
    global.valid = 1;
    global.format = FORMAT;
    global_dirty = 0; // whoever wrote it, it carries every setting
    global_failures = 0;
    return 1;
}

void store_poll(uint32_t now)
{
    if (!global_dirty || global_failures >= GLOBAL_TRIES)
        return;
    if (global_changed) { // the setters don't know the time
        global_changed = 0;
        global_changed_at = now;
    } else if (now - global_changed_at >= GLOBAL_REST_MS) {
        global_changed_at = now; // a failed write waits another rest
        write_global(); // on memory never formatted too: it becomes ours, as with a saved patch
    }
}

void store_flush(void)
{
    if (global_dirty && global_failures < GLOBAL_TRIES)
        write_global();
}

// Wipes the patch area (every record all zero: empty) and writes the global block for this format
static int format_patches(void)
{
    static const uint8_t zeros[256];
    for (uint32_t a = 0; a < PATCH_AREA; a += sizeof zeros)
        if (!eeprom_write(a, zeros, sizeof zeros))
            return 0;
    global.last_patch = 0;
    global.init_slot = 0; // every slot is empty: a later save there mustn't become the init by surprise
    return write_global();
}

static int formatted;

void store_init(void)
{
    uint8_t b[GLOBAL_SIZE];
    eeprom_read(GLOBAL_ADDRESS, b, sizeof b);
    global.valid = get32(b) == GLOBAL_MAGIC && get32(b + 12) == checksum(b, 12);
    int ext = global.valid && get32(b + 28) == checksum(b + GLOBAL_EXT, 28 - GLOBAL_EXT); // 0.10.0's bytes 19-27
    global.init_slot = ext && b[19] <= PATCH_COUNT ? b[19] : 0;
    global.last_patch = global.valid ? get16(b + 4) : 0;
    global.format = global.valid ? b[7] : 0;
    global.cc[0] = global.valid ? b[8] : 0;
    global.cc[1] = global.valid ? b[9] : 0;
    global.midi_channel = global.valid && b[10] <= 16 ? b[10] : 0;
    global.no_pickup = global.valid && b[11] == 1;
    global.bpm = get16(b + 16);
    global.clock_source = b[18];
    if (!global.valid || global.bpm < 300 || global.bpm > 3000) // 30-300 BPM
        global.bpm = 1200;
    if (!global.valid || global.clock_source > 2)
        global.clock_source = 0;
    if (global.last_patch >= PATCH_COUNT)
        global.last_patch = 0;
    if (global.valid && global.format != FORMAT) // our older layout: the patches can't be read, so start clean
        formatted = format_patches();
}

int store_ready(void) { return global.valid; }

int store_formatted(void) { return formatted; }

int store_format(void) { return format_patches(); }

int store_cc_number(int which) { return global.cc[which & 1]; }

int store_midi_channel(void) { return global.midi_channel; }

void store_set_midi_channel(int channel)
{
    if (channel < 0 || channel > 16 || global.midi_channel == channel)
        return;
    global.midi_channel = (uint8_t)channel;
    global_mark();
}

int store_pickup(void) { return !global.no_pickup; }

void store_set_pickup(int on)
{
    if ((on == 0) == global.no_pickup)
        return;
    global.no_pickup = !on;
    global_mark();
}

int store_clock_source(void) { return global.clock_source; }
int store_bpm_tenths(void) { return global.bpm; }

void store_set_clock(int source, int bpm_tenths)
{
    if (source < 0 || source > 2 || bpm_tenths < 300 || bpm_tenths > 3000)
        return;
    if (global.clock_source == source && global.bpm == bpm_tenths)
        return;
    global.clock_source = (uint8_t)source;
    global.bpm = (uint16_t)bpm_tenths;
    global_mark();
}

int store_init_patch(void) { return global.init_slot; }

void store_set_init_patch(int slot_plus_1)
{
    if (slot_plus_1 < 0 || slot_plus_1 > PATCH_COUNT || slot_plus_1 == global.init_slot)
        return;
    global.init_slot = (uint8_t)slot_plus_1;
    global_mark();
}

void store_set_cc_number(int which, int number)
{
    if (number < 0 || number > 127 || global.cc[which & 1] == number)
        return;
    global.cc[which & 1] = (uint8_t)number;
    global_mark();
}

static uint32_t record_address(int n) { return (uint32_t)n * RECORD_SIZE; }

static int read_ours(int n, uint8_t *b) // the patch half
{
    if (n < 0 || n >= PATCH_COUNT)
        return 0;
    eeprom_read(record_address(n), b, OFF_CHECKSUM + 4);
    return get32(b) == PATCH_MAGIC && get32(b + OFF_CHECKSUM) == checksum(b, OFF_CHECKSUM);
}

slot_kind_t store_slot_kind(int n)
{
    uint8_t b[OFF_CHECKSUM + 4];
    if (n < 0 || n >= PATCH_COUNT)
        return SLOT_EMPTY;
    eeprom_read(record_address(n), b, sizeof b);
    if (get32(b) == PATCH_MAGIC)
        return get32(b + OFF_CHECKSUM) == checksum(b, OFF_CHECKSUM) ? SLOT_OURS : SLOT_CORRUPT;
    for (uint32_t i = 0; i < sizeof b; i++)
        if (b[i])
            return SLOT_CORRUPT;
    return SLOT_EMPTY;
}

int store_load_patch(int n, patch_t *patch)
{
    uint8_t b[OFF_CHECKSUM + 4];
    if (!read_ours(n, b))
        return 0;
    *patch = (patch_t){0};
    for (int i = 0; i < PATCH_NAME; i++)
        patch->name[i] = (char)b[OFF_NAME + i];
    patch->layer_mode = b[OFF_MODE];
    patch->split = b[OFF_SPLIT];
    for (int w = 0; w < 2; w++) {
        patch->lfo_sync[w] = b[OFF_LFO_SYNC + w];
        patch->lfo_trigger[w] = b[OFF_LFO_TRIGGER + w];
        patch->lfo_delay[w] = b[OFF_LFO_DELAY + w];
        patch->lfo_fade[w] = b[OFF_LFO_FADE + w];
        patch->lfo_cycles[w] = b[OFF_LFO_CYCLES + w];
    }
    patch->keytrack = b[OFF_KEYTRACK];
    patch->drift = b[OFF_DRIFT];
    patch->morph = b[OFF_MORPH];
    patch->scale = b[OFF_SCALE];
    patch->voice_mode = b[OFF_VOICE_MODE];
    patch->bend_range = b[OFF_BEND];
    patch->detune = b[OFF_DETUNE];
    patch->layer_voices = b[OFF_LAYER_VOICES];
    patch->layer_link = b[OFF_LINK];
    patch->lfo_restart_keys = b[OFF_LFO_RESTART];
    patch->route_both[0] = get16(b + OFF_ROUTE_BOTH);
    patch->route_both[1] = get16(b + OFF_ROUTE_BOTH + 2);
    patch->unison_voices = b[OFF_UNISON_VOICES];
    patch->arp_range = b[OFF_ARP_RANGE];
    patch->arp_extend = b[OFF_ARP_EXTEND];
    patch->arp_chaos = b[OFF_ARP_CHAOS];
    for (int l = 0; l < 2; l++)
        for (int i = 0; i < CHORD_BYTES; i++)
            patch->chord[l][i] = b[OFF_CHORD + CHORD_BYTES * l + i];
    for (int l = 0; l < 2; l++)
        for (int r = 0; r < ROUTES; r++)
            patch->route_via[l][r] = get16(b + OFF_ROUTE_VIA + 2 * (l * ROUTES + r));
    for (int l = 0; l < 2; l++)
        for (int r = 0; r < ROUTES; r++)
            patch->route_fine[l][r] = (int8_t)b[OFF_ROUTE_FINE + l * ROUTES + r];
    for (int l = 0; l < 2; l++)
        patch->osc_tune[l] = b[OFF_OSC_TUNE + l] & 0x0F;
    patch->switches = get16(b + OFF_SWITCHES);
    for (int l = 0; l < 2; l++) {
        patch->lfo2_dests[l] = b[OFF_LFO2_DESTS + l];
        patch->env_cycle[l] = b[OFF_CYCLE + l];
        patch->env_trigger[l] = b[OFF_ENV_TRIGGER + l];
        if (!b[OFF_UP_CYCLES])
            patch->env_trigger[l] &= (uint8_t)~patch->env_cycle[l];
        patch->env_dahdsr[l] = b[OFF_ENV_DAHDSR + l];
        patch->env_tempo[l] = b[OFF_ENV_TEMPO + l];
        patch->env_curve[l] = b[OFF_ENV_CURVE + l];
        for (int w = 0; w < 2; w++) {
            patch->step_count[l][w] = b[OFF_STEP_COUNTS + 2 * l + w];
            patch->step_pattern[l][w] = get16(b + OFF_STEPS + 2 * (2 * l + w));
            patch->lfo_shape[l][w] = b[OFF_SHAPES + 2 * l + w];
            patch->macro_offset[l][w] = (int8_t)b[OFF_MACRO + 2 * l + w];
        }
        for (int e = 0; e < 4; e++) {
            patch->env_sync[l][e] = b[OFF_ENV_SYNC + 4 * l + e];
            patch->env_delay[l][e] = b[OFF_ENV_DELAY + 4 * l + e];
            patch->env_hold[l][e] = b[OFF_ENV_HOLD + 4 * l + e];
            patch->env_count[l][e] = b[OFF_ENV_COUNT + 4 * l + e];
        }
        for (int i = 0; i < POT_COUNT; i++)
            patch->pots[l][i] = i == POT_MASTER_VOL ? 0 : get16(b + OFF_POTS + 2 * (l * POT_COUNT + i));
        for (int i = 0; i < EXTRA_PARAMS; i++)
            patch->extra[l][i] = get16(b + OFF_EXTRA + 2 * (l * EXTRA_PARAMS + i));
        for (int r = 0; r < ROUTES; r++) {
            const uint8_t *x = b + OFF_ROUTES + 3 * (l * ROUTES + r);
            patch->routes[l][r].source = x[0];
            patch->routes[l][r].dest = x[1];
            patch->routes[l][r].depth = (int8_t)x[2];
        }
    }
    return 1;
}

int store_save_patch(int n, const patch_t *patch)
{
    uint8_t b[OFF_CHECKSUM + 4] = {0};
    if (n < 0 || n >= PATCH_COUNT)
        return 0;
    put32(b, PATCH_MAGIC);
    for (int i = 0; i < PATCH_NAME && patch->name[i]; i++)
        b[OFF_NAME + i] = (uint8_t)patch->name[i];
    b[OFF_MODE] = patch->layer_mode;
    b[OFF_SPLIT] = patch->split;
    for (int w = 0; w < 2; w++) {
        b[OFF_LFO_SYNC + w] = patch->lfo_sync[w];
        b[OFF_LFO_TRIGGER + w] = patch->lfo_trigger[w];
        b[OFF_LFO_DELAY + w] = patch->lfo_delay[w];
        b[OFF_LFO_FADE + w] = patch->lfo_fade[w];
        b[OFF_LFO_CYCLES + w] = patch->lfo_cycles[w];
    }
    b[OFF_KEYTRACK] = patch->keytrack;
    b[OFF_DRIFT] = patch->drift;
    b[OFF_MORPH] = patch->morph;
    b[OFF_SCALE] = patch->scale;
    b[OFF_VOICE_MODE] = patch->voice_mode;
    b[OFF_BEND] = patch->bend_range;
    b[OFF_DETUNE] = patch->detune;
    b[OFF_LAYER_VOICES] = patch->layer_voices;
    b[OFF_LINK] = patch->layer_link;
    b[OFF_LFO_RESTART] = patch->lfo_restart_keys;
    put16(b + OFF_ROUTE_BOTH, patch->route_both[0]);
    put16(b + OFF_ROUTE_BOTH + 2, patch->route_both[1]);
    b[OFF_UNISON_VOICES] = patch->unison_voices;
    b[OFF_ARP_RANGE] = patch->arp_range;
    b[OFF_ARP_EXTEND] = patch->arp_extend;
    b[OFF_ARP_CHAOS] = patch->arp_chaos;
    for (int l = 0; l < 2; l++)
        for (int i = 0; i < CHORD_BYTES; i++)
            b[OFF_CHORD + CHORD_BYTES * l + i] = patch->chord[l][i];
    for (int l = 0; l < 2; l++)
        for (int r = 0; r < ROUTES; r++)
            put16(b + OFF_ROUTE_VIA + 2 * (l * ROUTES + r), patch->route_via[l][r]);
    for (int l = 0; l < 2; l++)
        for (int r = 0; r < ROUTES; r++)
            b[OFF_ROUTE_FINE + l * ROUTES + r] = (uint8_t)patch->route_fine[l][r];
    b[OFF_UP_CYCLES] = 1;
    for (int l = 0; l < 2; l++)
        b[OFF_OSC_TUNE + l] = patch->osc_tune[l] & 0x0F;
    put16(b + OFF_SWITCHES, patch->switches);
    for (int l = 0; l < 2; l++) {
        b[OFF_LFO2_DESTS + l] = patch->lfo2_dests[l];
        b[OFF_CYCLE + l] = patch->env_cycle[l];
        b[OFF_ENV_TRIGGER + l] = patch->env_trigger[l];
        b[OFF_ENV_DAHDSR + l] = patch->env_dahdsr[l];
        b[OFF_ENV_TEMPO + l] = patch->env_tempo[l];
        b[OFF_ENV_CURVE + l] = patch->env_curve[l];
        for (int w = 0; w < 2; w++) {
            b[OFF_STEP_COUNTS + 2 * l + w] = patch->step_count[l][w];
            put16(b + OFF_STEPS + 2 * (2 * l + w), patch->step_pattern[l][w]);
            b[OFF_SHAPES + 2 * l + w] = patch->lfo_shape[l][w];
            b[OFF_MACRO + 2 * l + w] = (uint8_t)patch->macro_offset[l][w];
        }
        for (int e = 0; e < 4; e++) {
            b[OFF_ENV_SYNC + 4 * l + e] = patch->env_sync[l][e];
            b[OFF_ENV_DELAY + 4 * l + e] = patch->env_delay[l][e];
            b[OFF_ENV_HOLD + 4 * l + e] = patch->env_hold[l][e];
            b[OFF_ENV_COUNT + 4 * l + e] = patch->env_count[l][e];
        }
        for (int i = 0; i < POT_COUNT; i++)
            put16(b + OFF_POTS + 2 * (l * POT_COUNT + i), i == POT_MASTER_VOL ? 0 : patch->pots[l][i]);
        for (int i = 0; i < EXTRA_PARAMS; i++)
            put16(b + OFF_EXTRA + 2 * (l * EXTRA_PARAMS + i), patch->extra[l][i]);
        for (int r = 0; r < ROUTES; r++) {
            uint8_t *x = b + OFF_ROUTES + 3 * (l * ROUTES + r);
            x[0] = patch->routes[l][r].source;
            x[1] = patch->routes[l][r].dest;
            x[2] = (uint8_t)patch->routes[l][r].depth;
        }
    }
    _Static_assert(OFF_ENV_HOLD + 2 * 4 <= OFF_POTS, "envelope settings overflow");
    _Static_assert(OFF_POTS + 2 * 2 * POT_COUNT <= OFF_EXTRA, "pots overflow");
    _Static_assert(OFF_EXTRA + 2 * 2 * EXTRA_PARAMS <= OFF_ROUTES, "extra params overflow");
    _Static_assert(OFF_ROUTES + 3 * 2 * ROUTES <= OFF_ENV_COUNT, "routes overflow");
    // the fields from 336 on, in offset order: each ends where the next begins, the last before the reserved bytes
    _Static_assert(OFF_ENV_COUNT + 2 * 4 <= OFF_UP_CYCLES, "envelope counts overflow");
    _Static_assert(OFF_UP_CYCLES + 1 <= OFF_ENV_TEMPO, "up-cycles flag overflow");
    _Static_assert(OFF_ENV_TEMPO + 2 <= OFF_LINK, "envelope tempo overflow");
    _Static_assert(OFF_LINK + 1 <= OFF_LFO_CYCLES, "link overflow");
    _Static_assert(OFF_LFO_CYCLES + 2 <= OFF_LFO_RESTART, "LFO cycles overflow");
    _Static_assert(OFF_LFO_RESTART + 1 <= OFF_ROUTE_BOTH, "LFO restart overflow");
    _Static_assert(OFF_ROUTE_BOTH + 2 * 2 <= OFF_UNISON_VOICES, "route Both overflow");
    _Static_assert(OFF_UNISON_VOICES + 1 <= OFF_ROUTE_VIA, "unison voices overflow");
    _Static_assert(OFF_ROUTE_VIA + 2 * 2 * ROUTES <= OFF_ROUTE_FINE, "route Via overflow");
    _Static_assert(OFF_ROUTE_FINE + 2 * ROUTES <= OFF_ARP_RANGE, "route tenths overflow");
    _Static_assert(OFF_ARP_RANGE + 1 <= OFF_ARP_EXTEND && OFF_ARP_EXTEND + 1 <= OFF_ARP_CHAOS, "arp overflow");
    _Static_assert(OFF_ARP_CHAOS + 1 <= OFF_CHORD && OFF_CHORD + 2 * CHORD_BYTES <= OFF_RESERVED, "chords overflow");
    _Static_assert(OFF_RESERVED <= OFF_CHECKSUM, "reserved bytes overlap");
    _Static_assert(SEQ_OFF + SEQ_SIZE == RECORD_SIZE && SEQ_DATA + SEQ_BYTES <= SEQ_CHECKSUM, "record size");
    put32(b + OFF_CHECKSUM, checksum(b, OFF_CHECKSUM));
    if (!eeprom_write(record_address(n), b, sizeof b))
        return 0;
    // Memory never formatted (fresh from stock): a patch of ours makes it ours, so the last patch and the settings
    // are kept from now on. The other slots stay as they are.
    return global.valid || write_global();
}

// Stock patch body offsets (unpacked bytes; u16 little-endian)
static const struct { uint8_t offset; uint8_t pot; } stock_pots[] = {
    {5, POT_FREQ_A}, {7, POT_A_LEVEL}, {9, POT_A_PW}, {11, POT_FREQ_B}, {13, POT_B_LEVEL}, {15, POT_B_PW},
    {17, POT_FINE}, {19, POT_CUTOFF}, {21, POT_RESONANCE}, {23, POT_FIL_ENV_AMT}, {25, POT_FIL_REL},
    {27, POT_FIL_SUS}, {29, POT_FIL_DEC}, {31, POT_FIL_ATT}, {33, POT_AMP_REL}, {35, POT_AMP_SUS},
    {37, POT_AMP_DEC}, {39, POT_AMP_ATT}, {41, POT_PMOD_FIL_ENV}, {43, POT_PMOD_OSC_B}, {45, POT_LFO_FREQ},
    {47, POT_LFO_AMOUNT}, {49, POT_GLIDE}, {142, POT_NOISE},
};
static const struct { uint8_t offset; uint8_t sw; } stock_switches[] = {
    {55, SWITCH_A_SAW}, {56, SWITCH_A_TRI}, {57, SWITCH_A_SQUARE}, {58, SWITCH_B_SAW}, {59, SWITCH_B_TRI},
    {60, SWITCH_B_SQUARE}, {61, SWITCH_SYNC}, {62, SWITCH_PMOD_FREQ_A}, {63, SWITCH_PMOD_FILTER}, {71, SWITCH_UNISON},
};

static void convert_stock(const uint8_t *b, patch_t *p)
{
    *p = (patch_t){0};
    for (int l = 0; l < 2; l++) {
        for (int i = 0; i < POT_COUNT; i++)
            p->pots[l][i] = 0x8000; // centre for anything the stock patch doesn't have (M TUNE)
        for (unsigned i = 0; i < sizeof stock_pots / sizeof stock_pots[0]; i++)
            p->pots[l][stock_pots[i].pot] = get16(b + stock_pots[i].offset);
        p->extra[l][PARAM_LFO2_RATE - POT_COUNT] = 0x8000;
        p->lfo_shape[l][0] = b[64] == 0; // LFO shape: pulse becomes our square
    }
    for (unsigned i = 0; i < sizeof stock_switches / sizeof stock_switches[0]; i++)
        if (b[stock_switches[i].offset])
            p->switches |= 1u << stock_switches[i].sw;
    if (b[66] & 1) // LFO targets: frequency, filter, pulse width
        p->switches |= 1u << SWITCH_LFO_FREQ;
    if (b[66] & 2)
        p->switches |= 1u << SWITCH_LFO_FILTER;
    if (b[66] & 4)
        p->switches |= 1u << SWITCH_LFO_PW;
    p->keytrack = b[67] <= 2 ? b[67] : 0;
    p->split = 60;
    for (int i = 0; i < PATCH_NAME && b[150 + i] >= 0x20 && b[150 + i] < 0x7F; i++) // the name starts at 150
        p->name[i] = (char)b[150 + i];
}


int store_factory_name(int f, char name[PATCH_NAME + 1])
{
    uint8_t b[STOCK_SIZE];
    if (f < 0 || f >= FACTORY_COUNT)
        return 0;
    eeprom_read(FACTORY_ADDRESS + (uint32_t)f * STOCK_SIZE, b, STOCK_SIZE);
    if (!is_stock(b))
        return 0;
    int i = 0;
    for (; i < PATCH_NAME && b[150 + i] >= 0x20 && b[150 + i] < 0x7F; i++)
        name[i] = (char)b[150 + i];
    while (i > 0 && name[i - 1] == ' ')
        i--;
    name[i] = 0;
    return 1;
}

int store_import_factory(int f, int n)
{
    uint8_t b[STOCK_SIZE];
    if (f < 0 || f >= FACTORY_COUNT || n < 0 || n >= PATCH_COUNT)
        return 0;
    eeprom_read(FACTORY_ADDRESS + (uint32_t)f * STOCK_SIZE, b, STOCK_SIZE);
    if (!is_stock(b))
        return 0;
    patch_t p;
    convert_stock(b, &p);
    if (!store_save_patch(n, &p))
        return -1;
    // The sequence half too: it loads on its own, and the slot's old steps would play the new sound
    static const uint8_t zeros[SEQ_SIZE];
    return eeprom_write(record_address(n) + SEQ_OFF, zeros, SEQ_SIZE) ? 1 : -1;
}

int store_rename(int n, const char *name)
{
    patch_t p;
    if (!store_load_patch(n, &p))
        return 0;
    int i = 0;
    for (; i < PATCH_NAME && name[i]; i++)
        p.name[i] = name[i];
    for (; i <= PATCH_NAME; i++)
        p.name[i] = 0;
    return store_save_patch(n, &p);
}

int store_clear(int n)
{
    static const uint8_t zeros[256];
    if (n < 0 || n >= PATCH_COUNT)
        return 0;
    for (uint32_t a = 0; a < RECORD_SIZE; a += sizeof zeros)
        if (!eeprom_write(record_address(n) + a, zeros, sizeof zeros))
            return 0;
    return 1;
}

int store_load_sequence(int n)
{
    uint8_t b[SEQ_SIZE];
    if (n < 0 || n >= PATCH_COUNT)
        return 0;
    eeprom_read(record_address(n) + SEQ_OFF, b, SEQ_SIZE);
    if (get32(b) != SEQ_MAGIC || get32(b + SEQ_CHECKSUM) != checksum(b, SEQ_CHECKSUM))
        return 0;
    seq_clear();
    sequence_t *s = &sequence;
    s->length = b[SEQ_HEADER];
    s->lanes = b[SEQ_HEADER + 1];
    s->division = b[SEQ_HEADER + 2];
    s->bpm = b[SEQ_HEADER + 3];
    s->flags = b[SEQ_HEADER + 4];
    s->swing = b[SEQ_HEADER + 5];
    s->smooth = b[SEQ_HEADER + 6];
    s->arp = b[SEQ_HEADER + 7];
    seq_fix();
    const uint8_t *x = b + SEQ_DATA;
    for (int i = 0; i < s->length; i++, x += 4) {
        s->note[i] = x[0];
        s->velocity[i] = x[1];
        s->gate[i] = x[2];
        s->extra[i] = x[3];
    }
    for (int l = 0; l < s->lanes; l++)
        for (int i = 0; i < s->length; i++)
            s->lane[l][i] = *x++;
    return 1;
}

int store_save_sequence(int n)
{
    uint8_t b[SEQ_SIZE] = {0};
    if (n < 0 || n >= PATCH_COUNT)
        return 0;
    seq_fix();
    const sequence_t *s = &sequence;
    put32(b, SEQ_MAGIC);
    b[SEQ_HEADER] = s->length;
    b[SEQ_HEADER + 1] = s->lanes;
    b[SEQ_HEADER + 2] = s->division;
    b[SEQ_HEADER + 3] = s->bpm;
    b[SEQ_HEADER + 4] = s->flags;
    b[SEQ_HEADER + 5] = s->swing;
    b[SEQ_HEADER + 6] = s->smooth;
    b[SEQ_HEADER + 7] = s->arp;
    uint8_t *x = b + SEQ_DATA;
    for (int i = 0; i < s->length; i++, x += 4) {
        x[0] = s->note[i];
        x[1] = s->velocity[i];
        x[2] = s->gate[i];
        x[3] = s->extra[i];
    }
    for (int l = 0; l < s->lanes; l++)
        for (int i = 0; i < s->length; i++)
            *x++ = s->lane[l][i];
    put32(b + SEQ_CHECKSUM, checksum(b, SEQ_CHECKSUM));
    return eeprom_write(record_address(n) + SEQ_OFF, b, SEQ_SIZE);
}

int store_finish_import(void) { return write_global(); }

int store_last_patch(void) { return global.last_patch; }

void store_set_last(int patch)
{
    if (global.last_patch == patch)
        return;
    global.last_patch = (uint16_t)patch;
    if (global.valid) // on memory never formatted, this alone doesn't make it ours: the next block written carries it
        write_global();
}
