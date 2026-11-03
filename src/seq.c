// The key-triggered step sequencer (seq.h). Timing runs on the engine's dt: a step lasts its clock division at the
// tempo (MIDI clock or the sequence's bpm), with swing; the gate ends a note part-way through its step, a tie holds
// it until the next one starts (legato). Notes go through the synth like keys, to the layers the trigger key
// would have played.
#include "seq.h"
#include "lfo.h"
#include "synth.h"

#define HELD_MAX 8
#define CHORD_WINDOW_S 0.03f
#define EXTEND_ROLL_STEPS 8 // how often Chaos decides again which added notes play

sequence_t sequence;

static struct {
    uint8_t note, velocity, layers; // layers: where the key would have played (synth_key_layers())
} held[HELD_MAX]; // keys the sequence took, the latest last
static int held_count;
static int playing, step, layers, ratchet, skipped, arp_index;
static uint8_t sounding[1 + SEQ_CHORD_MAX]; // the notes playing, the step's own first
static uint8_t sounding_held[1 + SEQ_CHORD_MAX]; // ignores the gate, and carries on if the next step plays it
static int sounding_count;
static float in_step; // seconds into the current step
static uint8_t base_note, base_velocity;
static uint32_t random_state = 0x6C078965u;
static int walk_note = -1, walk_dir = 1, walk_turn; // the walk: its last note, which way, where in two on and one back
static uint8_t extend_kept = 0xFF;                  // a bit per added note: it plays for now
static float arp_position;                          // where the arp's note sits among its notes, 0..1

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
    int chord = SEQ_CHORD(s->flags);
    chord = chord > SEQ_CHORD_MAX ? SEQ_CHORD_MAX : chord > s->lanes ? s->lanes : chord;
    s->flags = (uint8_t)((s->flags & ~(3u << SEQ_CHORD_SHIFT)) | (unsigned)chord << SEQ_CHORD_SHIFT);
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
    for (int i = 0; i < sounding_count; i++)
        synth_note_off_layers(sounding[i], layers);
    sounding_count = 0;
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

// The root of what's held, by its intervals, so an inversion finds it, and its third (3 or 4 semitones, 0: none): the
// first note with a third and a fifth above it, else with a third, else the lowest
static int chord_root(const uint8_t *sorted, int n, int *third)
{
    unsigned pcs = 0;
    for (int i = 0; i < n; i++)
        pcs |= 1u << sorted[i] % 12;
    for (int pass = 0; pass < 2; pass++)
        for (int i = 0; i < n; i++) {
            int r = sorted[i] % 12, t = pcs >> (r + 3) % 12 & 1 ? 3 : pcs >> (r + 4) % 12 & 1 ? 4 : 0;
            if (t && (pass || pcs >> (r + 7) % 12 & 1)) {
                *third = t;
                return r;
            }
        }
    *third = 0;
    return sorted[0] % 12;
}

// The pitch class wanted or, as it's not in the patch's scale, the nearest that is, upward first
static int in_key(int pc)
{
    for (int d = 0; d < 3; d++) {
        if (synth_in_scale(pc + d))
            return (pc + d) % 12;
        if (synth_in_scale(pc + 12 - d))
            return (pc + 12 - d) % 12;
    }
    return pc % 12;
}

// The notes the extension adds, low to high, each the first of its name above the chord. With no third to go by and no
// scale, a seventh or a ninth would be a guess: an octave and a fifth only.
static int extension_notes(const uint8_t *sorted, int n, uint8_t *out)
{
    int third, root = chord_root(sorted, n, &third), level = synth_arp_extend(), count = 0;
    int keyed = synth_scale() & 15;
    if (!third && !keyed && level > ARP_EXTEND_FIFTH)
        level = ARP_EXTEND_FIFTH;
    for (int k = 0; k < level; k++) {
        int interval = k == 0 ? 0 : k == 1 ? 7 : k == 2 ? (third == 3 || keyed ? 10 : 11) : 14;
        int pc = keyed && k ? in_key(root + interval) : (root + interval) % 12;
        int note = sorted[n - 1] + 1 + ((pc - sorted[n - 1] - 1) % 12 + 12) % 12;
        if (note <= 127 && extend_kept >> k & 1) {
            int at = count++;
            for (; at > 0 && out[at - 1] > note; at--)
                out[at] = out[at - 1];
            out[at] = (uint8_t)note;
        }
    }
    return count;
}

// What the arp goes through, low to high (as played: in that order, then the added notes), over its octaves
static int arp_pool(uint8_t *pool)
{
    uint8_t sorted[HELD_MAX], added[ARP_EXTEND_COUNT];
    int n = held_count;
    for (int i = 0; i < n; i++) { // insertion sort, low to high
        uint8_t v = held[i].note;
        int j = i;
        for (; j > 0 && sorted[j - 1] > v; j--)
            sorted[j] = sorted[j - 1];
        sorted[j] = v;
    }
    int extras = synth_arp_extend() ? extension_notes(sorted, n, added) : 0, count = 0, octaves = 1 + synth_arp_range();
    for (int o = 0; o < octaves; o++) {
        if (sequence.arp == ARP_PLAYED) {
            for (int i = 0; i < n + extras; i++) {
                int note = (i < n ? held[i].note : added[i - n]) + 12 * o;
                if (note <= 127)
                    pool[count++] = (uint8_t)note;
            }
            continue;
        }
        for (int i = 0, e = 0; i < n || e < extras; ) { // both are in order
            int from_held = e == extras || (i < n && sorted[i] <= added[e]);
            int note = (from_held ? sorted[i++] : added[e++]) + 12 * o;
            if (note <= 127 && (o || !count || note > pool[count - 1]))
                pool[count++] = (uint8_t)note;
        }
    }
    if (octaves > 1 && sequence.arp != ARP_PLAYED) { // a chord wider than an octave lands among its own copies: in order, once each
        int kept = 0;
        for (int i = 0; i < count; i++) {
            uint8_t v = pool[i];
            int j = kept;
            for (; j > 0 && pool[j - 1] > v; j--)
                ;
            if (j > 0 && pool[j - 1] == v)
                continue;
            for (int m = kept; m > j; m--)
                pool[m] = pool[m - 1];
            pool[j] = v;
            kept++;
        }
        count = kept;
    }
    return count;
}

static float chance(void) { return (float)(random32() >> 16) / 65536.0f; }

// A line: two notes on and one back, turning at the ends. Chaos turns steps into jumps, drawn towards the middle. It goes
// from wherever it is, so a new chord follows on from its nearest note.
static int walk_index(const uint8_t *pool, int n, float chaos)
{
    if (walk_note < 0 || n < 2)
        return 0;
    int pos = 0, best = 256;
    for (int i = 0; i < n; i++) {
        int d = pool[i] - walk_note;
        d = d < 0 ? -d : d;
        if (d < best) {
            best = d;
            pos = i;
        }
    }
    if (chaos > 0 && chance() < chaos) {
        walk_turn = 0;
        int leap = 2 + (int)(chance() * (1.0f + chaos * 2.0f));
        float up = 0.5f + ((float)(n - 1) / 2.0f - (float)pos) / (float)n; // nearer the bottom, more likely up
        walk_dir = chance() < up ? 1 : -1;
        pos += walk_dir * leap;
    } else {
        pos += walk_turn == 2 ? -walk_dir : walk_dir;
        walk_turn = (walk_turn + 1) % 3;
    }
    if (pos >= n) {
        pos = 2 * (n - 1) - pos;
        walk_dir = -1;
    } else if (pos < 0) {
        pos = -pos;
        walk_dir = 1;
    }
    return pos < 0 ? 0 : pos >= n ? n - 1 : pos;
}

// Arpeggio: the next note in the arp's order, and where it sits among them. A ratchet repeats a walk's note.
static uint8_t arp_note(int repeat)
{
    uint8_t pool[(HELD_MAX + ARP_EXTEND_COUNT) * (ARP_RANGE_MAX + 1)];
    if (!held_count) {
        arp_position = 0;
        return base_note;
    }
    if (repeat && sequence.arp == ARP_WALK && walk_note >= 0)
        return (uint8_t)walk_note;
    float chaos = synth_arp_chaos(layers);
    if (!repeat && arp_index % EXTEND_ROLL_STEPS == 0) { // Chaos decides afresh which added notes play
        extend_kept = 0;
        for (int k = 0; k < ARP_EXTEND_COUNT; k++)
            if (chaos <= 0 || chance() >= chaos)
                extend_kept |= (uint8_t)(1u << k);
    }
    int n = arp_pool(pool), i;
    if (!n) { // every held note past 127, an octave up
        arp_position = 0;
        return base_note;
    }
    switch (sequence.arp) {
    case ARP_DOWN: i = n - 1 - arp_index % n; break;
    case ARP_UP_DOWN: { // up, then down without repeating the ends
        int period = n > 1 ? 2 * n - 2 : 1, k = arp_index % period;
        i = k < n ? k : period - k;
        break;
    }
    case ARP_RANDOM: i = (int)(random32() % (uint32_t)n); break;
    case ARP_WALK: i = walk_index(pool, n, chaos); break;
    default: i = arp_index % n; break;
    }
    if (sequence.arp != ARP_WALK && chaos > 0 && chance() < chaos)
        i = (int)(random32() % (uint32_t)n);
    int below = 0;
    for (int k = 0; k < n; k++)
        below += pool[k] < pool[i];
    arp_position = n > 1 ? (float)below / (float)(n - 1) : 0;
    walk_note = pool[i];
    return pool[i];
}

static int has_note(const uint8_t *notes, int count, int note)
{
    for (int i = 0; i < count; i++)
        if (notes[i] == note)
            return 1;
    return 0;
}

static uint8_t clamp_note(int note) { return (uint8_t)(note < 0 ? 0 : note > 127 ? 127 : note); }

// The gate's end: the notes that don't hold stop
static void release_unheld(void)
{
    int kept = 0;
    for (int i = 0; i < sounding_count; i++) {
        if (sounding_held[i]) {
            sounding[kept] = sounding[i];
            sounding_held[kept++] = 1;
        } else
            synth_note_off_layers(sounding[i], layers);
    }
    sounding_count = kept;
}

static int was_held(int note)
{
    for (int i = 0; i < sounding_count; i++)
        if (sounding[i] == note && sounding_held[i])
            return 1;
    return 0;
}

// Plays the step's note and its chord notes, offsets from it
static void play_note(int retrigger)
{
    uint8_t notes[1 + SEQ_CHORD_MAX], held[1 + SEQ_CHORD_MAX];
    int extra = sequence.extra[step], tie = (sequence.gate[step] & 0x7F) >= SEQ_GATE_TIE, single = SEQ_RATCHETS(extra) == 1;
    int count = 0, chord = SEQ_CHORD(sequence.flags);
    int note = (int)(sequence.arp ? arp_note(retrigger) : base_note) + (int)sequence.note[step] - 64;
    held[count] = single && (tie || SEQ_HOLDS(extra) & 1);
    notes[count++] = clamp_note(note);
    for (int c = 0; c < chord; c++) {
        int value = sequence.lane[sequence.lanes - chord + c][step];
        uint8_t n = clamp_note(notes[0] + value - SEQ_LANE_ZERO);
        if (value != SEQ_LANE_ZERO && !has_note(notes, count, n)) {
            held[count] = single && (tie || SEQ_HOLDS(extra) >> (c + 1) & 1);
            notes[count++] = n;
        }
    }
    uint8_t velocity = sequence.velocity[step] ? sequence.velocity[step] : base_velocity;
    synth_set_arp_position(sequence.arp ? arp_position : 0);
    for (int i = 0; i < count; i++) // a held note into the same note just carries on
        if (retrigger || !was_held(notes[i]))
            synth_note_on_layers(notes[i], velocity, layers);
    synth_set_arp_position(0);
    for (int i = 0; i < sounding_count; i++)
        if (!has_note(notes, count, sounding[i]))
            synth_note_off_layers(sounding[i], layers); // after the new notes: legato when held
    for (int i = 0; i < count; i++) {
        sounding[i] = notes[i];
        sounding_held[i] = held[i];
    }
    sounding_count = count;
}

static void play_step(void)
{
    int gate = sequence.gate[step];
    ratchet = 0;
    // a rest, or a step its probability skips this time
    skipped = !(gate & 0x80) || (int)(random32() % 8) >= SEQ_PROBABILITY(sequence.extra[step]);
    if (skipped) {
        release();
        return;
    }
    play_note(0);
    if (sequence.arp)
        arp_index++;
}

int seq_takes_layer(int layer)
{
    int target = sequence.flags >> SEQ_LAYER_SHIFT & 3;
    return sequence.flags & SEQ_ENABLED && (!target || target >> layer & 1);
}

int seq_key_on(uint8_t note, uint8_t velocity, int key_layers)
{
    int target = sequence.flags >> SEQ_LAYER_SHIFT & 3, taken = target ? key_layers & target : key_layers;
    if (!(sequence.flags & SEQ_ENABLED) || !taken)
        return 0;
    if (held_count == HELD_MAX)
        return taken;
    int busy = 0; // the layers with a key already down
    for (int i = 0; i < held_count; i++)
        busy |= held[i].layers;
    synth_lfos_key(taken, busy);
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
        walk_note = -1;
        walk_dir = 1;
        walk_turn = 0;
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
    if (sounding_count && in_step - (float)ratchet * part >= part * (float)gate / 100.0f)
        release_unheld();
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
    if (!playing || lane >= sequence.lanes - SEQ_CHORD(sequence.flags))
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
    for (int c = 1; c <= SEQ_CHORD(sequence.flags); c++)
        sequence.lane[sequence.lanes - c][recorded] = SEQ_LANE_ZERO; // one note: chords are entered on the web page
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
