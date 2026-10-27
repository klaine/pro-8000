// Clock sync: the interval between timing clocks is averaged, and a position in ticks advances continuously
// between them (snapped to the count on every tick, and held back from running more than a tick ahead while the
// clock runs), so the LFO moves smoothly but stays on the grid. Start resets the count, so the phase lines up with
// the bar. If the clock stops, the LFO carries on at the last tempo.
//
// Retrigger: the shared phase resets on the first key after all were released, or on every key; in per-voice
// mode each voice runs its own phase from its note-on. With the clock and no retrigger, the phase follows the
// grid; with a retrigger, the rate follows the clock but the phase starts at the key.
#include "lfo.h"
#include "clock.h"
#include "fastmath.h"
#include "regs.h"
#include "sh.h"

#define TICKS_PER_QUARTER 24

lfo_sync_t lfo_sync = LFO_SYNC_FREE;
lfo_trigger_t lfo_trigger = LFO_TRIGGER_FREE;

static const struct {
    float quarters; // LFO period
    char name[5];
} divisions[] = {
    {32, "8bAr"}, {16, "4bAr"}, {8, "2bAr"}, {4, "1bAr"}, {2, "1-2"}, {1.5f, "1-4d"}, {1, "1-4"},
    {2.0f / 3, "1-4t"}, {0.75f, "1-8d"}, {0.5f, "1-8"}, {1.0f / 3, "1-8t"}, {0.375f, "16d"}, {0.25f, "1-16"},
    {1.0f / 6, "16t"}, {0.125f, "1-32"},
};
#define DIVISION_COUNT ((int)(sizeof divisions / sizeof divisions[0]))

static struct {
    float shared_phase, voice_phase[VOICE_COUNT];
} lfos[LFO_COUNT];
static uint32_t ticks, last_tick;
static float tick_interval; // seconds; 0 until two ticks have arrived
static float position;       // in ticks, continuous

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
        lfos[i].shared_phase = 0;
}

void lfo_clock_stop(void) {}

int lfo_division(float knob)
{
    int i = (int)(knob * (float)(DIVISION_COUNT - 1) + 0.5f);
    return i < 0 ? 0 : i >= DIVISION_COUNT ? DIVISION_COUNT - 1 : i;
}

const char *lfo_division_name(int division) { return divisions[division].name; }

void lfo_note_on(int lfo, int voice, int first)
{
    if (lfo_trigger == LFO_TRIGGER_EVERY_KEY || (lfo_trigger == LFO_TRIGGER_FIRST_KEY && first))
        lfos[lfo].shared_phase = 0;
    lfos[lfo].voice_phase[voice] = 0;
}

static float wrap(float phase) { return phase - (float)(int)phase; }

void lfo_update(int lfo, float dt, float knob)
{
    int synced = lfo_sync == LFO_SYNC_CLOCK && tick_interval > 0;
    float rate;
    if (synced) {
        float quarters = divisions[lfo_division(knob)].quarters;
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

    if (synced && lfo_trigger == LFO_TRIGGER_FREE)
        lfos[lfo].shared_phase = wrap(position / (TICKS_PER_QUARTER * divisions[lfo_division(knob)].quarters));
    else
        lfos[lfo].shared_phase = wrap(lfos[lfo].shared_phase + rate * dt);
    for (int v = 0; v < VOICE_COUNT; v++)
        lfos[lfo].voice_phase[v] = wrap(lfos[lfo].voice_phase[v] + rate * dt);
}

float lfo_value(int lfo, int voice, int square)
{
    float phase = voice >= 0 && lfo_trigger == LFO_TRIGGER_PER_VOICE ? lfos[lfo].voice_phase[voice]
                                                                     : lfos[lfo].shared_phase;
    if (square)
        return phase < 0.5f ? 1.0f : -1.0f;
    return phase < 0.5f ? 4 * phase - 1 : 3 - 4 * phase;
}
