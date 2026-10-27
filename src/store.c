// Patch record (256 bytes, little-endian):
//   0  "P8P1"            magic and format
//   4  name[16]          0-padded
//   20 layer mode, split note, LFO sync, LFO retrigger
//   24 switches (u32)    bit s = switch s engaged
//   28 keyboard tracking 0 off, 1 half, 2 full
//   32 pots[2][26] (u16) layer 1, then layer 2
//   136 extra[2][8] (u16) Env 3 and Env 4 ADSR per layer
//   168 routes[2][8]     source, destination, depth (int8) per layer
//   216 LFO 2 sync, LFO 2 retrigger
//   218 LFO shapes + 1   [layer][LFO 1, LFO 2]; 0 = not stored (before 0.6.0): LFO 1 from the shape switch's bit,
//                        LFO 2 triangle
//   222 LFO 2 destinations per layer (LFO2_TO_* bits)
//   224 extra[2][8..9] (u16) LFO 2 rate and amount per layer
//   232..251             reserved, zero (patches saved before 0.5.0 have zeros from 136: no routes)
//   252 checksum (u32)
// Zeros in a newer field always mean what older firmware did.
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
#define OFF_EXTRA 136
#define OFF_ROUTES 168
#define OFF_LFO2 216
#define OFF_SHAPES 218
#define OFF_LFO2_DESTS 222
#define OFF_EXTRA2 224
#define OFF_CHECKSUM 252
#define EXTRA_FIRST 8 // extra params stored at OFF_EXTRA; the rest at OFF_EXTRA2
#define FACTORY_ADDRESS 0x19000u

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

static int is_stock(const uint8_t *b) { return b[0] == 0xA5 && b[1] == 0x16 && b[2] == 0x61 && b[3] == 0x00; }

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
    if (is_stock(b))
        return SLOT_STOCK;
    for (uint32_t i = 0; i < PATCH_SIZE; i++)
        if (b[i])
            return SLOT_CORRUPT;
    return SLOT_EMPTY;
}

static int read_ours(int n, uint8_t *b)
{
    if (n < 0 || n >= PATCH_COUNT)
        return 0;
    eeprom_read((uint32_t)n * PATCH_SIZE, b, PATCH_SIZE);
    return get32(b) == PATCH_MAGIC && get32(b + OFF_CHECKSUM) == checksum(b, OFF_CHECKSUM);
}

static int extra_offset(int layer, int i)
{
    return i < EXTRA_FIRST ? OFF_EXTRA + 2 * (layer * EXTRA_FIRST + i)
                           : OFF_EXTRA2 + 2 * (layer * (EXTRA_PARAMS - EXTRA_FIRST) + i - EXTRA_FIRST);
}

int store_load_patch(int n, patch_t *patch)
{
    uint8_t b[PATCH_SIZE];
    if (!read_ours(n, b))
        return 0;
    for (int i = 0; i < PATCH_NAME; i++)
        patch->name[i] = (char)b[OFF_NAME + i];
    patch->name[PATCH_NAME] = 0;
    patch->layer_mode = b[OFF_SETTINGS];
    patch->split = b[OFF_SETTINGS + 1];
    patch->lfo_sync[0] = b[OFF_SETTINGS + 2];
    patch->lfo_trigger[0] = b[OFF_SETTINGS + 3];
    patch->lfo_sync[1] = b[OFF_LFO2];
    patch->lfo_trigger[1] = b[OFF_LFO2 + 1];
    patch->switches = get32(b + OFF_SWITCHES);
    patch->keytrack = b[OFF_KEYTRACK];
    for (int l = 0; l < 2; l++) {
        for (int i = 0; i < POT_COUNT; i++)
            patch->pots[l][i] = get16(b + OFF_POTS + 2 * (l * POT_COUNT + i));
        for (int i = 0; i < EXTRA_PARAMS; i++)
            patch->extra[l][i] = get16(b + extra_offset(l, i));
        for (int r = 0; r < ROUTES; r++) {
            const uint8_t *x = b + OFF_ROUTES + 3 * (l * ROUTES + r);
            patch->routes[l][r] = (route_t){x[0], x[1], (int8_t)x[2]};
        }
        for (int w = 0; w < 2; w++) {
            uint8_t shape = b[OFF_SHAPES + 2 * l + w];
            patch->lfo_shape[l][w] = shape ? (uint8_t)(shape - 1)
                                   : w == 0 && (patch->switches >> SWITCH_LFO_SHAPE & 1) ? 1 : 0; // square, triangle
        }
        patch->lfo2_dests[l] = b[OFF_LFO2_DESTS + l];
    }
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
    b[OFF_SETTINGS + 2] = patch->lfo_sync[0];
    b[OFF_SETTINGS + 3] = patch->lfo_trigger[0];
    b[OFF_LFO2] = patch->lfo_sync[1];
    b[OFF_LFO2 + 1] = patch->lfo_trigger[1];
    put32(b + OFF_SWITCHES, patch->switches);
    b[OFF_KEYTRACK] = patch->keytrack;
    for (int l = 0; l < 2; l++) {
        for (int i = 0; i < POT_COUNT; i++)
            put16(b + OFF_POTS + 2 * (l * POT_COUNT + i), patch->pots[l][i]);
        for (int i = 0; i < EXTRA_PARAMS; i++)
            put16(b + extra_offset(l, i), patch->extra[l][i]);
        for (int r = 0; r < ROUTES; r++) {
            uint8_t *x = b + OFF_ROUTES + 3 * (l * ROUTES + r);
            x[0] = patch->routes[l][r].source;
            x[1] = patch->routes[l][r].dest;
            x[2] = (uint8_t)patch->routes[l][r].depth;
        }
        for (int w = 0; w < 2; w++)
            b[OFF_SHAPES + 2 * l + w] = (uint8_t)(patch->lfo_shape[l][w] + 1);
        b[OFF_LFO2_DESTS + l] = patch->lfo2_dests[l];
    }
    _Static_assert(OFF_ROUTES + 3 * 2 * ROUTES <= OFF_LFO2, "routes overflow");
    _Static_assert(EXTRA_FIRST * 2 * 2 == OFF_ROUTES - OFF_EXTRA, "extra params overflow");
    _Static_assert(OFF_EXTRA2 + (EXTRA_PARAMS - EXTRA_FIRST) * 2 * 2 <= OFF_CHECKSUM, "patch record overflow");
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

int store_import_stock(int n)
{
    uint8_t b[PATCH_SIZE];
    eeprom_read((uint32_t)n * PATCH_SIZE, b, PATCH_SIZE);
    if (!is_stock(b))
        return 0;
    patch_t p;
    convert_stock(b, &p);
    return store_save_patch(n, &p) ? 1 : -1;
}

int store_factory_name(int f, char name[PATCH_NAME + 1])
{
    uint8_t b[PATCH_SIZE];
    if (f < 0 || f >= FACTORY_COUNT)
        return 0;
    eeprom_read(FACTORY_ADDRESS + (uint32_t)f * PATCH_SIZE, b, PATCH_SIZE);
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
    uint8_t b[PATCH_SIZE];
    if (f < 0 || f >= FACTORY_COUNT || n < 0 || n >= PATCH_COUNT)
        return 0;
    eeprom_read(FACTORY_ADDRESS + (uint32_t)f * PATCH_SIZE, b, PATCH_SIZE);
    if (!is_stock(b))
        return 0;
    patch_t p;
    convert_stock(b, &p);
    return store_save_patch(n, &p) ? 1 : -1;
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
    uint8_t b[PATCH_SIZE] = {0};
    return n >= 0 && n < PATCH_COUNT && eeprom_write((uint32_t)n * PATCH_SIZE, b, PATCH_SIZE);
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
