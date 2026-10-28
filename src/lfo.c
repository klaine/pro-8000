// Clock sync: the USB interrupt counts the timing clocks and stamps each with the cycle counter, so none is lost and
// none is late while the main loop is busy. The interval between them is averaged, and the position in ticks is the
// count plus the time since the last one (at most a tick ahead), so the LFO moves smoothly but stays on the grid.
// After Start the next clock is the downbeat, tick 0, so the phase lines up with the bar; after Continue it's where
// Song Position Pointer put it (or where the clock stopped), and the LFOs on the grid jump there. If the clock
// stops, the LFO carries on at the last tempo. Before any clock, a synced LFO runs its division at 120 BPM.
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
// The position wraps at 24 bars, a whole number of cycles of every division (the dotted ones too), so it keeps its
// precision in a float however long the clock runs
#define LOOP_TICKS 2304
// Rate modulation on a grid-locked LFO (clock sync, free retrigger) lets it run off the grid; when the modulation is
// back under this (octaves), the LFO runs up to REGRID_PULL × its phase error faster or slower until it's back.
#define RATE_MOD_THRESHOLD 0.01f
#define REGRID_PULL        2.0f

lfo_sync_t lfo_sync[2];
lfo_trigger_t lfo_trigger[2];
uint8_t lfo_delay[2], lfo_fade[2];

float lfo_seconds(uint8_t value) { return value ? 0.02f * fast_exp2((float)value * (8.0f / 99)) : 0; }

float lfo_fade_gain(int which, float age)
{
    if (!lfo_delay[which] && !lfo_fade[which])
        return 1.0f;
    float t = age - lfo_seconds(lfo_delay[which]), fade = lfo_seconds(lfo_fade[which]);
    return t <= 0 ? 0 : fade <= 0 || t >= fade ? 1.0f : t / fade;
}
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
// Written by the USB or DIN interrupt (lfo_clock_tick() and the others; same priority, so never two at once), read
// by the main loop (clock_update()). `ticks` is the song position of the next timing clock: after Start 0, the
// downbeat; `waiting` until that clock comes after Start or Continue.
static volatile uint32_t ticks, last_tick, starts, jumps, song_ticks;
static volatile uint8_t waiting, song_set;
static volatile float tick_interval; // seconds; 0 until two ticks have arrived
static uint32_t seen_ticks, seen_starts, seen_jumps, wait_began;
static int clock_running, hold; // hold: after Start or Continue, the LFOs on the grid wait where the next clock lands
static int wait_open;           // the hold window since the last Start or Continue hasn't expired yet
static float position;          // in ticks from the downbeat, continuous, modulo LOOP_TICKS
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
    waiting = 0;
    ticks++;
}

void lfo_clock_start(void)
{
    ticks = 0; // the next timing clock is the downbeat
    waiting = 1;
    song_set = 0;
    starts++; // clock_update() resets the phases
}

void lfo_clock_continue(void)
{
    if (song_set) // from where Song Position Pointer put it, else from where the clock stopped
        ticks = song_ticks;
    song_set = 0;
    waiting = 1;
    jumps++; // clock_update() puts the LFOs on the grid there
}

void lfo_clock_song_position(uint16_t sixteenths)
{
    song_ticks = (uint32_t)sixteenths * (TICKS_PER_QUARTER / 4);
    song_set = 1;
}

void lfo_clock_stop(void) {}

// Once per update (from LFO 0's, whichever LFOs are synced): the position from the interrupt's count and the time
// since the last tick. The clock counts as stopped two intervals after its last tick, which the main loop sees long
// before the cycle counter wraps; then the position carries on at the last tempo.
static void clock_update(float dt)
{
    uint32_t stamp, count;
    int wait;
    do {
        stamp = last_tick;
        count = ticks;
        wait = waiting;
    } while (stamp != last_tick || count != ticks); // a tick or a Start came in between
    if (starts != seen_starts || jumps != seen_jumps) {
        wait_began = DWT_CYCCNT;
        wait_open = 1;
    }
    if (starts != seen_starts)
        for (int i = 0; i < LFO_COUNT; i++)
            lfos[i].shared.phase = 0;
    seen_starts = starts;
    seen_jumps = jumps;
    float interval = tick_interval, since = (float)(DWT_CYCCNT - stamp) / (float)system_hz;
    if (count != seen_ticks) {
        seen_ticks = count;
        clock_running = 1;
    } else if (since > 2 * interval) {
        clock_running = 0;
    }
    // after Start or Continue, wait where the next clock lands (two intervals at most, in case none comes). The window
    // closes for good once it has expired: `waiting` stays set until a clock comes, and the cycle counter wraps every
    // 25 s, so the elapsed time alone would open it again.
    if (wait_open && (float)(DWT_CYCCNT - wait_began) / (float)system_hz >= 2 * interval)
        wait_open = 0;
    hold = wait && wait_open;
    if (hold)
        position = (float)(count % LOOP_TICKS);
    else if (clock_running && count)
        position = (float)((count - 1) % LOOP_TICKS) + (since < interval ? since / interval : 1.0f);
    else if (interval > 0 && (position += dt / interval) >= (float)LOOP_TICKS)
        position -= (float)LOOP_TICKS;
}

float lfo_rate_hz(float knob) { return 0.05f * fast_exp2(knob * 10.0f); }

int lfo_division(float knob)
{
    int i = (int)(knob * (float)(DIVISION_COUNT - 1) + 0.5f);
    return i < 0 ? 0 : i >= DIVISION_COUNT ? DIVISION_COUNT - 1 : i;
}

const char *lfo_division_name(int division) { return divisions[division].name; }

float lfo_division_quarters(int division) { return divisions[division].quarters; }

float lfo_quarter_seconds(void) { return tick_interval > 0 ? tick_interval * TICKS_PER_QUARTER : 0.5f; }

float lfo_beat_phase(void)
{
    float q = position / TICKS_PER_QUARTER;
    return q - (float)(int)q;
}

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
    if (lfo == 0)
        clock_update(dt);
    int synced = lfo_sync[which] == LFO_SYNC_CLOCK;
    float quarters = divisions[lfo_division(knob)].quarters;
    float rate = synced ? 1.0f / (lfo_quarter_seconds() * quarters) : lfo_rate_hz(knob); // 120 BPM until a clock
    lfos[lfo].rate = rate;

    phase_t *p = &lfos[lfo].shared;
    if (synced && tick_interval > 0 && lfo_trigger[which] == LFO_TRIGGER_FREE) { // on the grid
        float cycles = position / (TICKS_PER_QUARTER * quarters);
        float grid = cycles - (float)(int)cycles;
        float step = rate * dt;
        float error = grid - (p->phase + step); // against where the phase lands now, else it settles a pass ahead
        error -= error > 0.5f ? 1.0f : error < -0.5f ? -1.0f : 0.0f; // -0.5..0.5 of a cycle
        if (octaves > RATE_MOD_THRESHOLD || octaves < -RATE_MOD_THRESHOLD) {
            advance(p, step * fast_exp2(octaves)); // swept: smoothly off the grid
        } else if (hold || (error < 0.002f && error > -0.002f)) {
            if (grid < p->phase - 0.5f)
                new_cycle(p);
            p->phase = grid;
        } else {
            advance(p, step * (1.0f + REGRID_PULL * error)); // back onto the grid, about a cycle
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
