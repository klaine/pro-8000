#include "voice.h"
#include "sh.h"

#define CV_A440         0x894D // oscillator 1A, from the 0.0.2 sweep
#define CODES_PER_OCTAVE 6515
#define DETUNE_B        27     // about 5 cents
#define MIXER_LEVEL     0xA000u
#define FILTER_CUTOFF   0xC000u
#define LEVEL_MAX       0xFFFFu
#define ATTACK_MS       5
#define RELEASE_MS      150

typedef struct {
    uint8_t note;
    uint8_t gate;
    uint32_t level; // 0..LEVEL_MAX
    uint32_t started; // allocation order, for stealing the oldest
} voice_t;

static voice_t voices[VOICE_COUNT];
static uint32_t allocations;

static uint16_t note_cv(int note, int offset)
{
    int32_t cv = CV_A440 + (note - 69) * CODES_PER_OCTAVE / 12 + offset;
    return (uint16_t)(cv < 0 ? 0 : cv > 0xFFFF ? 0xFFFF : cv);
}

void voice_init(void)
{
    for (int v = 0; v < VOICE_COUNT; v++) {
        voices[v] = (voice_t){0};
        sh_set(CV_OSC_A1 + v, note_cv(60, 0));
        sh_set(CV_OSC_B1 + v, note_cv(60, DETUNE_B));
        sh_set(CV_FIL1 + v, FILTER_CUTOFF);
        sh_set(CV_AMP1 + v, 0);
    }
    sh_set(CV_VOL_A, MIXER_LEVEL);
    sh_set(CV_VOL_B, MIXER_LEVEL);
    sh_set(CV_PMOD_OSC_B, 0);
    sh_set(CV_NOISE, 0);
    sh_set(CV_EXT_FIL, 0);
    sh_set(CV_RESONANCE, 0);
    // as in p600fw with the square off: PW A at maximum, PW B at minimum, so no pulse bleeds through
    sh_set(CV_PW_A, 0xFFFFu);
    sh_set(CV_PW_B, 0);
    sh_gate(GATE_SAW_A, 1);
    sh_gate(GATE_SAW_B, 1);
}

// Prefers the voice already on this note, then a silent one, then the oldest.
static int allocate(uint8_t note)
{
    int best = -1;
    for (int v = 0; v < VOICE_COUNT; v++)
        if (voices[v].note == note && (voices[v].gate || voices[v].level))
            return v;
    for (int v = 0; v < VOICE_COUNT; v++)
        if (!voices[v].gate && !voices[v].level && (best < 0 || voices[v].started < voices[best].started))
            best = v;
    if (best >= 0)
        return best;
    for (int v = 0; v < VOICE_COUNT; v++)
        if (!voices[v].gate && (best < 0 || voices[v].started < voices[best].started))
            best = v;
    if (best >= 0)
        return best;
    best = 0;
    for (int v = 1; v < VOICE_COUNT; v++)
        if (voices[v].started < voices[best].started)
            best = v;
    return best;
}

void voice_note_on(uint8_t note)
{
    int v = allocate(note);
    voices[v].note = note;
    voices[v].gate = 1;
    voices[v].started = ++allocations;
    sh_set(CV_OSC_A1 + v, note_cv(note, 0));
    sh_set(CV_OSC_B1 + v, note_cv(note, DETUNE_B));
}

void voice_note_off(uint8_t note)
{
    for (int v = 0; v < VOICE_COUNT; v++)
        if (voices[v].gate && voices[v].note == note)
            voices[v].gate = 0;
}

void voice_all_off(void)
{
    for (int v = 0; v < VOICE_COUNT; v++)
        voices[v].gate = 0;
}

void voice_update(uint32_t ms)
{
    for (int v = 0; v < VOICE_COUNT; v++) {
        voice_t *voice = &voices[v];
        if (voice->gate) {
            uint32_t step = ms * LEVEL_MAX / ATTACK_MS;
            voice->level = voice->level + step > LEVEL_MAX ? LEVEL_MAX : voice->level + step;
        } else {
            uint32_t step = ms * LEVEL_MAX / RELEASE_MS;
            voice->level = voice->level > step ? voice->level - step : 0;
        }
        sh_set(CV_AMP1 + v, (uint16_t)voice->level);
    }
}

int voice_any_gate(void)
{
    for (int v = 0; v < VOICE_COUNT; v++)
        if (voices[v].gate)
            return 1;
    return 0;
}
