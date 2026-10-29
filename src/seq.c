// The key-triggered step sequencer (seq.h). Timing runs on the engine's dt: a step lasts its clock division at the
// tempo (MIDI clock or the sequence's bpm), with swing; the gate ends a note part-way through its step, a tie holds
// it until the next one starts (legato). Notes go through the synth like keys, to the layers the trigger key
// would have played.
#include "seq.h"
#include "lfo.h"
#include "synth.h"

#define HELD_MAX 8
#define CHORD_WINDOW_S 0.03f

sequence_t sequence;

static struct {
    uint8_t note, velocity, layers; // layers: where the key would have played (synth_key_layers())
} held[HELD_MAX]; // keys the sequence took, the latest last
static int held_count;
static int playing, step, layers, sounding = -1, tied, ratchet, skipped, arp_index;
static float in_step; // seconds into the current step
static uint8_t base_note, base_velocity;
static uint32_t random_state = 0x6C078965u;

static uint32_t random32(void) // xorshift32
{
    random_state ^= random_state << 13;
    random_state ^= random_state >> 17;
    random_state ^= random_state << 5;
    return random_state;
}

int seq_max_steps(int lanes)
{
    int n = SEQ_BYTES / (4 + lanes);
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
    if (s->arp >= ARP_COUNT)
        s->arp = ARP_OFF;
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
        synth_note_off_layers((uint8_t)sounding, layers);
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

// Arpeggio: the next held note in the arp's order
static uint8_t arp_note(void)
{
    uint8_t sorted[HELD_MAX];
    int n = held_count;
    if (!n)
        return base_note;
    for (int i = 0; i < n; i++) { // insertion sort, low to high
        uint8_t v = held[i].note;
        int j = i;
        for (; j > 0 && sorted[j - 1] > v; j--)
            sorted[j] = sorted[j - 1];
        sorted[j] = v;
    }
    int i;
    switch (sequence.arp) {
    case ARP_DOWN: i = n - 1 - arp_index % n; break;
    case ARP_UP_DOWN: { // up, then down without repeating the ends
        int period = n > 1 ? 2 * n - 2 : 1, k = arp_index % period;
        i = k < n ? k : period - k;
        break;
    }
    case ARP_RANDOM: i = (int)(random32() % (uint32_t)n); break;
    case ARP_PLAYED: return held[arp_index % n].note;
    default: i = arp_index % n; break;
    }
    return sorted[i];
}

static void play_note(int retrigger)
{
    int note = (int)(sequence.arp ? arp_note() : base_note) + (int)sequence.note[step] - 64;
    note = note < 0 ? 0 : note > 127 ? 127 : note;
    uint8_t velocity = sequence.velocity[step] ? sequence.velocity[step] : base_velocity;
    if (retrigger || !(tied && note == sounding)) { // a tie into the same note just carries on
        int previous = sounding;
        synth_note_on_layers((uint8_t)note, velocity, layers);
        if (previous >= 0 && previous != note)
            synth_note_off_layers((uint8_t)previous, layers); // after the new note: legato when tied
        sounding = note;
    }
}

static void play_step(void)
{
    int gate = sequence.gate[step];
    ratchet = 0;
    // a rest, or a step its probability skips this time
    skipped = !(gate & 0x80) || (int)(random32() % 8) >= SEQ_PROBABILITY(sequence.extra[step]);
    if (skipped) {
        release();
        tied = 0;
        return;
    }
    play_note(0);
    if (sequence.arp)
        arp_index++;
    tied = (gate & 0x7F) >= SEQ_GATE_TIE && SEQ_RATCHETS(sequence.extra[step]) == 1;
}

int seq_key_on(uint8_t note, uint8_t velocity, int key_layers)
{
    int target = sequence.flags >> SEQ_LAYER_SHIFT & 3, taken = target ? key_layers & target : key_layers;
    if (!(sequence.flags & SEQ_ENABLED) || !taken)
        return 0;
    if (held_count == HELD_MAX)
        return taken;
    held[held_count].note = note;
    held[held_count].velocity = velocity;
    held[held_count].layers = (uint8_t)taken;
    held_count++;
    base_note = note;
    base_velocity = velocity;
    if (!playing || (sequence.arp && step == 0 && in_step < CHORD_WINDOW_S)) {
        // a chord's notes arrive a few ms apart: an arpeggio restarts its first step until the chord is in
        if (!playing)
            layers = taken;
        playing = 1;
        step = 0;
        in_step = 0;
        arp_index = 0;
        play_step();
    }
    return taken;
}

int seq_key_off(uint8_t note, int key_layers)
{
    for (int i = 0; i < held_count; i++)
        if (held[i].note == note && held[i].layers & key_layers) {
            int taken = held[i].layers;
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
            return taken;
        }
    return 0;
}

void seq_update(float dt)
{
    if (!playing)
        return;
    in_step += dt;
    float length = step_seconds(step);
    int gate = sequence.gate[step] & 0x7F, ratchets = SEQ_RATCHETS(sequence.extra[step]);
    float part = length / (float)ratchets; // ratchets: the step played as 2-4 notes
    if (!skipped && ratchets > 1) {
        int now = (int)(in_step / part);
        if (now > ratchet && now < ratchets) {
            ratchet = now;
            release();
            play_note(1);
        }
    }
    if (sounding >= 0 && !tied && in_step - (float)ratchet * part >= part * (float)gate / 100.0f)
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

static int recorded, record_base = -1; // the first note recorded is offset 0

void seq_record_start(void)
{
    seq_stop();
    recorded = 0;
    record_base = -1;
}

static int record_step(int offset, uint8_t velocity, uint8_t gate)
{
    if (recorded >= seq_max_steps(sequence.lanes))
        return -1;
    sequence.note[recorded] = (uint8_t)(64 + (offset < -64 ? -64 : offset > 63 ? 63 : offset));
    sequence.velocity[recorded] = velocity;
    sequence.gate[recorded] = gate;
    sequence.extra[recorded] = 0;
    sequence.length = (uint8_t)++recorded;
    sequence.flags |= SEQ_ENABLED;
    return recorded;
}

int seq_record_note(uint8_t note, uint8_t velocity)
{
    if (record_base < 0)
        record_base = note;
    return record_step(note - record_base, velocity, 0x80 | 50);
}

int seq_record_rest(void) { return record_step(0, 0, 50); }
