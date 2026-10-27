// Clock sync: the interval between timing clocks is averaged, and a position in ticks advances continuously
// between them (snapped to the count on every tick, and held back from running more than a tick ahead while the
// clock runs), so the LFO moves smoothly but stays on the grid. Start resets the count, so the phase lines up with
// the bar. If the clock stops, the LFO carries on at the last tempo.
//
// Retrigger: the shared phase resets on the first key after all were released, or on every key; in per-voice
// mode each voice runs its own phase from its note-on. With the clock and no retrigger, the phase follows the
// grid; with a retrigger, the rate follows the clock but the phase starts at the key. Rate modulation sweeps the
// rate smoothly in every mode; a grid-locked LFO slides back onto the grid once the modulation stops.
//
// Every phase carries two random values for the random shapes: the one it holds (sample & hold) and the next one
// (drift glides towards it); both move on when the phase wraps or is retriggered.
#include "lfo.h"
#include "clock.h"
#include "fastmath.h"
#include "regs.h"
#include "sh.h"

#define TICKS_PER_QUARTER 24
// Rate modulation on a grid-locked LFO (clock sync, free retrigger) lets it run off the grid; when the modulation is
// back under this (octaves), the LFO runs up to REGRID_PULL × its phase error faster or slower until it's back.
#define RATE_MOD_THRESHOLD 0.01f
#define REGRID_PULL        2.0f

lfo_sync_t lfo_sync[2];
lfo_trigger_t lfo_trigger[2];
uint16_t lfo_pattern[LFO_COUNT] = {LFO_DEFAULT_PATTERN, LFO_DEFAULT_PATTERN, LFO_DEFAULT_PATTERN, LFO_DEFAULT_PATTERN};
uint8_t lfo_step_count[LFO_COUNT] = {LFO_DEFAULT_STEPS, LFO_DEFAULT_STEPS, LFO_DEFAULT_STEPS, LFO_DEFAULT_STEPS};

#define STEP_SLOPE_S 0.008f // a gate (LFO → VCA) opens over half of it: about 2 updates

static const struct {
    float quarters; // LFO period
    char name[5];
} divisions[] = {
    {32, "8bAr"}, {16, "4bAr"}, {8, "2bAr"}, {4, "1bAr"}, {2, "1-2"}, {1.5f, "1-4d"}, {1, "1-4"},
    {2.0f / 3, "1-4t"}, {0.75f, "1-8d"}, {0.5f, "1-8"}, {1.0f / 3, "1-8t"}, {0.375f, "16d"}, {0.25f, "1-16"},
    {1.0f / 6, "16t"}, {0.125f, "1-32"},
};
#define DIVISION_COUNT ((int)(sizeof divisions / sizeof divisions[0]))

static const char shape_names[LFO_SHAPE_COUNT][6] = {"trI", "PULS", "SA.UP", "SA.dn", "SInE", "S-H", "drFt", "StEP"};

typedef struct {
    float phase;
    float held, next; // random values, -1..1
} phase_t;

static struct {
    phase_t shared, voice[VOICE_COUNT];
    float rate; // Hz, from the last lfo_update()
} lfos[LFO_COUNT];
static uint32_t ticks, last_tick;
static float tick_interval; // seconds; 0 until two ticks have arrived
static float position;       // in ticks, continuous
static uint32_t random_state = 0x9E3779B9u;

static float random_value(void) // xorshift32, -1..1
{
    random_state ^= random_state << 13;
    random_state ^= random_state >> 17;
    random_state ^= random_state << 5;
    return (float)(int32_t)random_state / 2147483648.0f;
}

static void new_cycle(phase_t *p)
{
    p->held = p->next;
    p->next = random_value();
}

static void restart(phase_t *p)
{
    p->phase = 0;
    new_cycle(p);
}

// Adds `step` cycles; a wrap starts a new random cycle.
static void advance(phase_t *p, float step)
{
    p->phase += step;
    if (p->phase >= 1.0f) {
        p->phase -= (float)(int)p->phase;
        new_cycle(p);
    }
}

void lfo_clock_tick(uint32_t cycles)
{
    if (ticks > 0 || last_tick) {
        float measured = (float)(cycles - last_tick) / (float)system_hz;
        if (measured > 0.002f && measured < 0.25f) // 10-1250 BPM
            tick_interval = tick_interval > 0 && measured < 2 * tick_interval && measured > 0.5f * tick_interval
                          ? tick_interval * 0.9f + measured * 0.1f
                          : measured;
    }
    last_tick = cycles;
    ticks++;
    position = (float)ticks;
}

void lfo_clock_start(void)
{
    ticks = 0;
    position = 0;
    for (int i = 0; i < LFO_COUNT; i++)
        lfos[i].shared.phase = 0;
}

void lfo_clock_stop(void) {}

int lfo_division(float knob)
{
    int i = (int)(knob * (float)(DIVISION_COUNT - 1) + 0.5f);
    return i < 0 ? 0 : i >= DIVISION_COUNT ? DIVISION_COUNT - 1 : i;
}

const char *lfo_division_name(int division) { return divisions[division].name; }

float lfo_division_quarters(int division) { return divisions[division].quarters; }

float lfo_quarter_seconds(void) { return tick_interval > 0 ? tick_interval * TICKS_PER_QUARTER : 0.5f; }

_Static_assert(DIVISION_COUNT == LFO_DIVISIONS, "LFO_DIVISIONS");

const char *lfo_shape_name(lfo_shape_t shape) { return shape_names[shape < LFO_SHAPE_COUNT ? shape : 0]; }

void lfo_note_on(int lfo, int voice, int first)
{
    lfo_trigger_t trigger = lfo_trigger[lfo / 2];
    if (trigger == LFO_TRIGGER_EVERY_KEY || (trigger == LFO_TRIGGER_FIRST_KEY && first))
        restart(&lfos[lfo].shared);
    restart(&lfos[lfo].voice[voice]);
}

void lfo_update(int lfo, float dt, float knob, float octaves)
{
    int which = lfo / 2;
    int synced = lfo_sync[which] == LFO_SYNC_CLOCK && tick_interval > 0;
    float quarters = divisions[lfo_division(knob)].quarters;
    float rate;
    if (synced) {
        rate = 1.0f / (tick_interval * TICKS_PER_QUARTER * quarters);
        if (lfo == 0) { // the clock position advances once per update
            float since_tick = (float)(DWT_CYCCNT - last_tick) / (float)system_hz;
            position += dt / tick_interval;
            if (since_tick < 2 * tick_interval && position > (float)ticks + 1)
                position = (float)ticks + 1; // clock running: don't get ahead of the next tick
        }
    } else {
        rate = 0.05f * fast_exp2(knob * 10.0f); // 0.05-51 Hz
    }
    lfos[lfo].rate = rate;

    phase_t *p = &lfos[lfo].shared;
    if (synced && lfo_trigger[which] == LFO_TRIGGER_FREE) { // on the grid
        float cycles = position / (TICKS_PER_QUARTER * quarters);
        float grid = cycles - (float)(int)cycles;
        float error = grid - p->phase; // -0.5..0.5 of a cycle
        error -= error > 0.5f ? 1.0f : error < -0.5f ? -1.0f : 0.0f;
        if (octaves > RATE_MOD_THRESHOLD || octaves < -RATE_MOD_THRESHOLD) {
            advance(p, rate * dt * fast_exp2(octaves)); // swept: smoothly off the grid
        } else if (error < 0.002f && error > -0.002f) {
            if (grid < p->phase - 0.5f)
                new_cycle(p);
            p->phase = grid;
        } else {
            advance(p, rate * dt * (1.0f + REGRID_PULL * error)); // back onto the grid, about a cycle
        }
    } else {
        advance(p, rate * dt * (octaves != 0 ? fast_exp2(octaves) : 1.0f));
    }
}

void lfo_update_voice(int lfo, int voice, float dt, float octaves)
{
    advance(&lfos[lfo].voice[voice], lfos[lfo].rate * dt * (octaves != 0 ? fast_exp2(octaves) : 1.0f));
}

// sin(2π x) for x in [0, 1), within about 0.1%
static float sine(float x)
{
    float t = 2 * x - 1; // sin(2π x) = -sin(π t)
    float y = 4 * t * (1 - (t < 0 ? -t : t));
    y = 0.225f * (y * (y < 0 ? -y : y) - y) + y;
    return -y;
}

float lfo_value(int lfo, int voice, lfo_shape_t shape)
{
    const phase_t *p = voice >= 0 && lfo_trigger[lfo / 2] == LFO_TRIGGER_PER_VOICE ? &lfos[lfo].voice[voice]
                                                                                    : &lfos[lfo].shared;
    float x = p->phase;
    switch (shape) {
    case LFO_SQUARE: return x < 0.5f ? 1.0f : -1.0f;
    case LFO_RAMP_UP: return 2 * x - 1;
    case LFO_RAMP_DOWN: return 1 - 2 * x;
    case LFO_SINE: return -sine(x < 0.75f ? x + 0.25f : x - 0.75f); // -cos: starts low, like the triangle
    case LFO_SAMPLE_HOLD: return p->held;
    case LFO_DRIFT: {
        float s = x * x * (3 - 2 * x); // smoothstep
        return p->held + (p->next - p->held) * s;
    }
    case LFO_STEPS: {
        int count = lfo_step_count[lfo] < 1 || lfo_step_count[lfo] > LFO_MAX_STEPS ? LFO_DEFAULT_STEPS
                                                                                   : lfo_step_count[lfo];
        float position = x * (float)count;
        int step = (int)position;
        if (step >= count)
            step = count - 1;
        float now = lfo_pattern[lfo] >> step & 1 ? 1.0f : -1.0f;
        float before = lfo_pattern[lfo] >> (step ? step - 1 : count - 1) & 1 ? 1.0f : -1.0f;
        float slope = STEP_SLOPE_S * lfos[lfo].rate * (float)count; // the slope's share of a step
        float into = position - (float)step;
        return into >= slope ? now : before + (now - before) * into / slope;
    }
    default: return x < 0.5f ? 4 * x - 1 : 3 - 4 * x;
    }
}
