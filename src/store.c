// Patch record (256 bytes, little-endian):
//   0  "P8P1"            magic and format
//   4  name[16]          0-padded
//   20 layer mode, split note, LFO sync, LFO retrigger
//   24 switches (u32)    bit s = switch s engaged
//   28 keyboard tracking 0 off, 1 half, 2 full
//   32 pots[2][26] (u16) layer 1, then layer 2
//   136..251             reserved (mod routes), zero
//   252 checksum (u32)
// Stock patches start with a5 16 61 00 and the format byte 0x6D or 0x6F; their fields are documented in
// samstaton/pro800 (pro800syx.md) and converted here field for field. The value curves of our engine differ from
// the stock firmware's, so an imported sound is close rather than identical.
#include "store.h"
#include "eeprom.h"

#define PATCH_SIZE    256u
#define GLOBAL_ADDRESS 0x1F400u
#define PATCH_MAGIC   0x31503850u // "P8P1"
#define GLOBAL_MAGIC  0x31473850u // "P8G1"
#define OFF_NAME 4
#define OFF_SETTINGS 20
#define OFF_SWITCHES 24
#define OFF_KEYTRACK 28
#define OFF_POTS 32
#define OFF_CHECKSUM 252

static struct {
    int valid;
    uint16_t last_patch;
    uint8_t preset_mode;
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

void store_init(void)
{
    uint8_t b[16];
    eeprom_read(GLOBAL_ADDRESS, b, sizeof b);
    global.valid = get32(b) == GLOBAL_MAGIC && get32(b + 12) == checksum(b, 12);
    global.last_patch = global.valid ? get16(b + 4) : 0;
    global.preset_mode = global.valid ? b[6] : 0;
    if (global.last_patch >= PATCH_COUNT)
        global.last_patch = 0;
}

int store_ready(void) { return global.valid; }

slot_kind_t store_slot_kind(int n)
{
    uint8_t b[PATCH_SIZE];
    eeprom_read((uint32_t)n * PATCH_SIZE, b, PATCH_SIZE);
    if (get32(b) == PATCH_MAGIC)
        return get32(b + OFF_CHECKSUM) == checksum(b, OFF_CHECKSUM) ? SLOT_OURS : SLOT_CORRUPT;
    if (b[0] == 0xA5 && b[1] == 0x16 && b[2] == 0x61 && b[3] == 0x00)
        return SLOT_STOCK;
    for (uint32_t i = 0; i < PATCH_SIZE; i++)
        if (b[i])
            return SLOT_CORRUPT;
    return SLOT_EMPTY;
}

int store_load_patch(int n, patch_t *patch)
{
    uint8_t b[PATCH_SIZE];
    if (n < 0 || n >= PATCH_COUNT)
        return 0;
    eeprom_read((uint32_t)n * PATCH_SIZE, b, PATCH_SIZE);
    if (get32(b) != PATCH_MAGIC || get32(b + OFF_CHECKSUM) != checksum(b, OFF_CHECKSUM))
        return 0;
    for (int i = 0; i < PATCH_NAME; i++)
        patch->name[i] = (char)b[OFF_NAME + i];
    patch->name[PATCH_NAME] = 0;
    patch->layer_mode = b[OFF_SETTINGS];
    patch->split = b[OFF_SETTINGS + 1];
    patch->lfo_sync = b[OFF_SETTINGS + 2];
    patch->lfo_trigger = b[OFF_SETTINGS + 3];
    patch->switches = get32(b + OFF_SWITCHES);
    patch->keytrack = b[OFF_KEYTRACK];
    for (int l = 0; l < 2; l++)
        for (int i = 0; i < POT_COUNT; i++)
            patch->pots[l][i] = get16(b + OFF_POTS + 2 * (l * POT_COUNT + i));
    return 1;
}

int store_save_patch(int n, const patch_t *patch)
{
    uint8_t b[PATCH_SIZE] = {0};
    if (n < 0 || n >= PATCH_COUNT)
        return 0;
    put32(b, PATCH_MAGIC);
    for (int i = 0; i < PATCH_NAME && patch->name[i]; i++)
        b[OFF_NAME + i] = (uint8_t)patch->name[i];
    b[OFF_SETTINGS] = patch->layer_mode;
    b[OFF_SETTINGS + 1] = patch->split;
    b[OFF_SETTINGS + 2] = patch->lfo_sync;
    b[OFF_SETTINGS + 3] = patch->lfo_trigger;
    put32(b + OFF_SWITCHES, patch->switches);
    b[OFF_KEYTRACK] = patch->keytrack;
    for (int l = 0; l < 2; l++)
        for (int i = 0; i < POT_COUNT; i++)
            put16(b + OFF_POTS + 2 * (l * POT_COUNT + i), patch->pots[l][i]);
    put32(b + OFF_CHECKSUM, checksum(b, OFF_CHECKSUM));
    return eeprom_write((uint32_t)n * PATCH_SIZE, b, PATCH_SIZE);
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

int store_import_stock(int n)
{
    uint8_t b[PATCH_SIZE];
    eeprom_read((uint32_t)n * PATCH_SIZE, b, PATCH_SIZE);
    if (!(b[0] == 0xA5 && b[1] == 0x16 && b[2] == 0x61 && b[3] == 0x00))
        return 0;

    patch_t p = {0};
    for (int l = 0; l < 2; l++) {
        for (int i = 0; i < POT_COUNT; i++)
            p.pots[l][i] = 0x8000; // centre for anything the stock patch doesn't have (M TUNE)
        for (unsigned i = 0; i < sizeof stock_pots / sizeof stock_pots[0]; i++)
            p.pots[l][stock_pots[i].pot] = get16(b + stock_pots[i].offset);
    }
    for (unsigned i = 0; i < sizeof stock_switches / sizeof stock_switches[0]; i++)
        if (b[stock_switches[i].offset])
            p.switches |= 1u << stock_switches[i].sw;
    if (b[64] == 0) // LFO shape: pulse becomes our square
        p.switches |= 1u << SWITCH_LFO_SHAPE;
    if (b[66] & 1) // LFO targets: frequency, filter, pulse width
        p.switches |= 1u << SWITCH_LFO_FREQ;
    if (b[66] & 2)
        p.switches |= 1u << SWITCH_LFO_FILTER;
    if (b[66] & 4)
        p.switches |= 1u << SWITCH_LFO_PW;
    p.keytrack = b[67] <= 2 ? b[67] : 0;
    p.split = 60;
    for (int i = 0; i < PATCH_NAME && b[150 + i] >= 0x20 && b[150 + i] < 0x7F; i++) // the name starts at 150
        p.name[i] = (char)b[150 + i];
    return store_save_patch(n, &p) ? 1 : -1;
}

static int write_global(void)
{
    uint8_t b[16] = {0};
    put32(b, GLOBAL_MAGIC);
    put16(b + 4, global.last_patch);
    b[6] = global.preset_mode;
    put32(b + 12, checksum(b, 12));
    if (!eeprom_write(GLOBAL_ADDRESS, b, sizeof b))
        return 0;
    global.valid = 1;
    return 1;
}

int store_finish_import(void) { return write_global(); }

int store_last_patch(void) { return global.last_patch; }

int store_preset_mode(void) { return global.preset_mode; }

void store_set_last(int patch, int preset_mode)
{
    if (!global.valid || (global.last_patch == patch && global.preset_mode == preset_mode))
        return;
    global.last_patch = (uint16_t)patch;
    global.preset_mode = (uint8_t)preset_mode;
    write_global();
}
