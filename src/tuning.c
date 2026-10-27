// Each oscillator and filter is measured at fixed CVs (0x2000, 0x3000 ... 0xF000) through TUNE_CPR, which sees the
// voice mix after the per-voice VCAs and before the master VCA, so tuning runs with the master volume at 0.
// Oscillators play a saw on their own mixer channel; filters are measured self-oscillating (resonance at maximum,
// both mixer channels off). A note's CV is interpolated linearly in log frequency between the two nearest points;
// the response is close to exponential (6515 codes per octave on 1A), so that's accurate between points.
//
// The tables live in the patch EEPROM (store.h), with a magic number and checksum, so they survive firmware
// updates (the bootloader erases flash sectors 4 and 5 with every update); main() tunes when it finds none.
#include "tuning.h"
#include <stddef.h>
#include "clock.h"
#include "fastmath.h"
#include "regs.h"
#include "eeprom.h"
#include "sh.h"
#include "store.h"
#include "tune.h"

#define TUNING_MAGIC   0x4E555450u // "PTUN"
#define TUNING_FORMAT  2 // 2: in the EEPROM

#define FILTER_POINTS 12   // filters up to 0xD000 (about 15 kHz self-oscillating); higher is ultrasonic
#define WINDOW_S      0.05f // measuring time per point
#define OSC_STEP_OCTAVES    0.63f // per 0x1000 of CV, measured: 4096 / 6515
#define FILTER_STEP_OCTAVES 0.69f
#define SETTLE_MS     30
#define FIRST_SETTLE_MS 60 // after switching voice or mixer
#define CV_A440       0x894D // nominal oscillator 1A curve, used until tuned
#define CODES_PER_OCTAVE 6515.0f
#define LOG2_440      8.78135971f

typedef struct {
    uint32_t magic;
    uint32_t format;
    float log2_hz[TUNING_ROWS][TUNING_POINTS]; // 0 = not measured
    uint32_t checksum;
} store_t;

static store_t store;
_Static_assert(sizeof(store_t) <= STORE_TUNING_SIZE, "tuning tables outgrow their EEPROM area");
static int valid;

uint16_t tuning_point_cv(int point) { return (uint16_t)(0x2000 + 0x1000 * point); }

static uint32_t checksum(const store_t *s)
{
    const uint32_t *w = (const uint32_t *)s;
    uint32_t sum = 0x12345678u;
    for (uint32_t i = 0; i < offsetof(store_t, checksum) / 4; i++)
        sum = (sum << 5 | sum >> 27) ^ w[i];
    return sum;
}

int tuning_load(void)
{
    eeprom_read(STORE_TUNING_ADDRESS, (uint8_t *)&store, sizeof store);
    valid = store.magic == TUNING_MAGIC && store.format == TUNING_FORMAT && store.checksum == checksum(&store);
    return valid;
}

int tuning_valid(void) { return valid; }

static void save(void)
{
    store.magic = TUNING_MAGIC;
    store.format = TUNING_FORMAT;
    store.checksum = checksum(&store);
    eeprom_write(STORE_TUNING_ADDRESS, (const uint8_t *)&store, sizeof store);
    tuning_load();
}

static void settle(uint32_t ms)
{
    uint32_t start = DWT_CYCCNT, cycles = ms * (system_hz / 1000u);
    while (DWT_CYCCNT - start < cycles) {
        IWDG_KR = IWDG_KR_REFRESH;
        sh_refresh();
    }
}

// Measures one row: `cv` is the CV that sweeps (oscillator or filter), the rest is already set up. It sweeps
// down from the top, where the comparator is clean; below about 30 Hz it can add a stray edge every period and
// read an octave high. Each point is expected one step (`step_octaves`) below the last good one, and a reading
// more than a third of a step off is dropped: the notes down there extrapolate from the good points instead.
static int measure_row(int row, cv_t cv, int points, float step_octaves)
{
    float last_y = 0; // log2 Hz of the last good point
    int last_p = 0, good = 0;
    for (int p = TUNING_POINTS - 1; p >= 0; p--) {
        store.log2_hz[row][p] = 0;
        if (p >= points)
            continue;
        sh_set(cv, tuning_point_cv(p));
        settle(good == 0 ? FIRST_SETTLE_MS : SETTLE_MS);

        float expected_y = last_y - step_octaves * (float)(last_p - p);
        uint32_t periods = 64, timeout_ms = 600; // top point: frequency unknown
        if (good) {
            float estimate = 1.0f;
            for (float e = expected_y; e >= 1.0f; e -= 1.0f) // 2^expected_y, close enough for sizing
                estimate *= 2.0f;
            float n = estimate * WINDOW_S;
            periods = n < 6 ? 6 : n > TUNE_MAX_PERIODS ? TUNE_MAX_PERIODS : (uint32_t)n;
            timeout_ms = (uint32_t)(3000.0f * (float)periods / estimate) + 50;
        }
        tune_result_t r = tune_measure(periods, timeout_ms, 0);
        if (!r.cycles)
            continue;
        float y = fast_log2((float)r.periods * (float)system_hz / (float)r.cycles);
        if (good && (y >= last_y || y - expected_y > step_octaves / 3 || expected_y - y > step_octaves / 3))
            continue;
        store.log2_hz[row][p] = y;
        last_y = y;
        last_p = p;
        good++;
    }
    return good >= 2;
}

int tuning_run(void)
{
    int rows_ok = 0;

    sh_set(CV_MASTER_VOL, 0);
    sh_set(CV_PMOD_OSC_B, 0);
    sh_set(CV_NOISE, 0);
    sh_set(CV_EXT_FIL, 0);
    sh_set(CV_RESONANCE, 0);
    sh_set(CV_PW_A, 0xFFFFu); // square off
    sh_set(CV_PW_B, 0);
    for (int g = 0; g < GATE_COUNT; g++)
        sh_gate((gate_t)g, 0);
    for (int v = 0; v < VOICE_COUNT; v++) {
        sh_set(CV_AMP1 + v, 0);
        sh_set(CV_FIL1 + v, 0xFFFFu);
    }

    for (int osc = 0; osc < 2; osc++) {
        sh_gate(GATE_SAW_A, osc == 0);
        sh_gate(GATE_SAW_B, osc == 1);
        sh_set(CV_VOL_A, osc == 0 ? 0xFFFFu : 0);
        sh_set(CV_VOL_B, osc == 1 ? 0xFFFFu : 0);
        for (int v = 0; v < VOICE_COUNT; v++) {
            sh_set(CV_AMP1 + v, 0xFFFFu);
            rows_ok += measure_row((osc ? TUNING_ROW_OSC_B : TUNING_ROW_OSC_A) + v,
                                   (cv_t)((osc ? CV_OSC_B1 : CV_OSC_A1) + v), TUNING_POINTS, OSC_STEP_OCTAVES);
            sh_set(CV_AMP1 + v, 0);
        }
    }

    sh_gate(GATE_SAW_A, 0);
    sh_gate(GATE_SAW_B, 0);
    sh_set(CV_VOL_A, 0);
    sh_set(CV_VOL_B, 0);
    sh_set(CV_RESONANCE, 0xFFFFu);
    for (int v = 0; v < VOICE_COUNT; v++) {
        sh_set(CV_AMP1 + v, 0xFFFFu);
        rows_ok += measure_row(TUNING_ROW_FILTER + v, (cv_t)(CV_FIL1 + v), FILTER_POINTS, FILTER_STEP_OCTAVES);
        sh_set(CV_AMP1 + v, 0);
        sh_set(CV_FIL1 + v, 0xFFFFu);
    }
    sh_set(CV_RESONANCE, 0);
    settle(5);

    save();
    return rows_ok;
}

uint32_t tuning_centihertz(int row, int point)
{
    float y = store.log2_hz[row][point];
    if (!valid || y <= 0)
        return 0;
    return (uint32_t)(fast_exp2(y) * 100.0f + 0.5f);
}

uint16_t tuning_note_cv(int row, float note)
{
    float target = LOG2_440 + (note - 69.0f) / 12.0f, cv;
    int points[TUNING_POINTS], n = 0;
    if (valid)
        for (int p = 0; p < TUNING_POINTS; p++)
            if (store.log2_hz[row][p] > 0)
                points[n++] = p;

    if (n >= 2) {
        // the first segment whose upper end reaches the target; below the first point or above the last, the
        // end segment extrapolates
        int i = 0;
        while (i < n - 2 && store.log2_hz[row][points[i + 1]] < target)
            i++;
        float y0 = store.log2_hz[row][points[i]], y1 = store.log2_hz[row][points[i + 1]];
        float c0 = tuning_point_cv(points[i]), c1 = tuning_point_cv(points[i + 1]);
        cv = c0 + (target - y0) * (c1 - c0) / (y1 - y0);
    } else {
        cv = CV_A440 + (note - 69.0f) * CODES_PER_OCTAVE / 12.0f;
    }
    return (uint16_t)(cv < 0 ? 0 : cv > 65535.0f ? 65535 : cv + 0.5f);
}
