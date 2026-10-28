// The key-triggered step sequencer (seq.h). Timing runs on the engine's dt: a step lasts its clock division at the
// tempo (MIDI clock or the sequence's bpm), with swing; the gate ends a note part-way through its step, a tie holds
// it until the next one starts (legato). Notes go through the synth like keys, to the layers the trigger key
// would have played.
#include "seq.h"
#include "lfo.h"
#include "synth.h"

#define HELD_MAX 8

sequence_t sequence;

static struct {
    uint8_t note, velocity;
} held[HELD_MAX]; // keys the sequence took, the latest last
static int held_count;
static int playing, step, layers, sounding = -1, tied;
static float in_step; // seconds into the current step
static uint8_t base_note, base_velocity;

int seq_max_steps(int lanes)
{
    int n = SEQ_BYTES / (3 + lanes);
    return n > SEQ_MAX_STEPS ? SEQ_MAX_STEPS : n;
}

void seq_fix(void)
{
    sequence_t *s = &sequence;
    if (s->lanes > SEQ_LANES)
        s->lanes = SEQ_LANES;
    if (s->length < 1)
        s->length = 1;
    if (s->length > seq_max_steps(s->lanes))
        s->length = (uint8_t)seq_max_steps(s->lanes);
    if (s->division >= LFO_DIVISIONS)
        s->division = 12; // 1/16
    if (s->bpm < 30)
        s->bpm = 120;
    if (s->swing > 75)
        s->swing = 75;
}

void seq_clear(void)
{
    seq_stop();
    sequence = (sequence_t){.length = 16, .division = 12, .bpm = 120, .flags = SEQ_CLOCK};
    for (int i = 0; i < SEQ_MAX_STEPS; i++) {
        sequence.note[i] = 64;
        sequence.gate[i] = 0x80 | 50;
        for (int l = 0; l < SEQ_LANES; l++)
            sequence.lane[l][i] = SEQ_LANE_ZERO;
    }
}

static void release(void)
{
    if (sounding >= 0)
        synth_note_off((uint8_t)sounding);
    sounding = -1;
    tied = 0;
}

void seq_stop(void)
{
    release();
    playing = 0;
    held_count = 0;
}

static float step_seconds(int n)
{
    float quarter = sequence.flags & SEQ_CLOCK ? lfo_quarter_seconds() : 60.0f / (float)sequence.bpm;
    float length = lfo_division_quarters(sequence.division) * quarter;
    float swing = (float)sequence.swing / 100.0f;
    return n % 2 ? length * (1.0f - swing) : length * (1.0f + swing);
}

static void play_step(void)
{
    int gate = sequence.gate[step];
    if (!(gate & 0x80)) { // a rest
        release();
        return;
    }
    int note = (int)base_note + (int)sequence.note[step] - 64;
    note = note < 0 ? 0 : note > 127 ? 127 : note;
    uint8_t velocity = sequence.velocity[step] ? sequence.velocity[step] : base_velocity;
    if (!(tied && note == sounding)) { // a tie into the same note just carries on
        int previous = sounding;
        synth_note_on_layers((uint8_t)note, velocity, layers);
        if (previous >= 0 && previous != note)
            synth_note_off((uint8_t)previous); // after the new note: legato when tied
        sounding = note;
    }
    tied = (gate & 0x7F) >= SEQ_GATE_TIE;
}

int seq_key_on(uint8_t note, uint8_t velocity, int key_layers)
{
    int target = sequence.flags >> SEQ_LAYER_SHIFT & 3;
    if (!(sequence.flags & SEQ_ENABLED) || (target && !(key_layers & target)))
        return 0;
    if (held_count == HELD_MAX)
        return 1;
    held[held_count].note = note;
    held[held_count].velocity = velocity;
    held_count++;
    base_note = note;
    base_velocity = velocity;
    if (!playing) {
        layers = target ? target : key_layers;
        playing = 1;
        step = 0;
        in_step = 0;
        play_step();
    }
    return 1;
}

int seq_key_off(uint8_t note)
{
    for (int i = 0; i < held_count; i++)
        if (held[i].note == note) {
            for (int j = i; j < held_count - 1; j++)
                held[j] = held[j + 1];
            held_count--;
            if (held_count) { // back to the latest key still held, from the next step
                base_note = held[held_count - 1].note;
                base_velocity = held[held_count - 1].velocity;
            } else {
                release();
                playing = 0;
            }
            return 1;
        }
    return 0;
}

void seq_update(float dt)
{
    if (!playing)
        return;
    in_step += dt;
    float length = step_seconds(step);
    int gate = sequence.gate[step] & 0x7F;
    if (sounding >= 0 && !tied && in_step >= length * (float)gate / 100.0f)
        release();
    while (in_step >= length) {
        in_step -= length;
        if (++step >= sequence.length) {
            step = 0;
            if (sequence.flags & SEQ_ONE_SHOT) {
                release();
                playing = 0; // until every key is let go and one is pressed again
                return;
            }
        }
        play_step();
        length = step_seconds(step);
    }
}

float seq_lane(int lane)
{
    if (!playing || lane >= sequence.lanes)
        return 0;
    float v = (float)sequence.lane[lane][step];
    if (sequence.smooth >> lane & 1) {
        int next = step + 1 >= sequence.length ? 0 : step + 1;
        float u = in_step / step_seconds(step);
        v += ((float)sequence.lane[lane][next] - v) * (u > 1 ? 1 : u);
    }
    v = (v - SEQ_LANE_ZERO) / 63.0f;
    return v < -1 ? -1 : v > 1 ? 1 : v;
}
