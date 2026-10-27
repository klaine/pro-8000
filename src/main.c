// Hardware test: every voice in turn. After a one-second gap, each voice plays half a second of oscillator A
// (saw), half a second of oscillator B (saw) and half a second of its filter self-oscillating, all at CV 0x8000,
// so a recording shows 24 tones and any channel that doesn't sound. The panel goes dark in the gap (it stays lit
// throughout if the crystal/PLL didn't start). Holding any panel button mutes.
#include "clock.h"
#include "panel.h"
#include "regs.h"
#include "sh.h"

#define GAP_MS     1000
#define SEGMENT_MS 500
#define SEGMENT_COUNT (VOICE_COUNT * 3)

#define MASTER_VOLUME 0x6000u // about 37%, kept low on purpose
#define MIXER_LEVEL   0xC000u
#define FILTER_OPEN   0xE000u
#define TEST_CV       0x8000u

static volatile uint32_t ms_ticks;

void SysTick_Handler(void) { ms_ticks++; }

typedef enum { PART_OSC_A, PART_OSC_B, PART_FILTER } part_t;

static void voice_setup(void)
{
    for (int v = 0; v < VOICE_COUNT; v++) {
        sh_set(CV_OSC_A1 + v, TEST_CV);
        sh_set(CV_OSC_B1 + v, TEST_CV);
        sh_set(CV_FIL1 + v, FILTER_OPEN);
        sh_set(CV_AMP1 + v, 0);
    }
    sh_set(CV_PMOD_OSC_B, 0);
    sh_set(CV_NOISE, 0);
    sh_set(CV_EXT_FIL, 0);
    // as in p600fw with the square off: PW A at maximum, PW B at minimum, so no pulse bleeds through
    sh_set(CV_PW_A, 0xFFFFu);
    sh_set(CV_PW_B, 0);
    sh_set(CV_MASTER_VOL, 0);
}

// Sets up the mixer, gates and filters for one part of the test; only `voice`'s VCA opens.
static void play(int voice, part_t part, int muted)
{
    sh_gate(GATE_SAW_A, part == PART_OSC_A);
    sh_gate(GATE_SAW_B, part == PART_OSC_B);
    sh_set(CV_VOL_A, part == PART_OSC_A ? MIXER_LEVEL : 0);
    sh_set(CV_VOL_B, part == PART_OSC_B ? MIXER_LEVEL : 0);
    sh_set(CV_RESONANCE, part == PART_FILTER ? 0xFFFFu : 0);
    for (int v = 0; v < VOICE_COUNT; v++) {
        sh_set(CV_FIL1 + v, part == PART_FILTER ? TEST_CV : FILTER_OPEN);
        sh_set(CV_AMP1 + v, v == voice && !muted ? 0xFFFFu : 0);
    }
}

int main(void)
{
    sh_safe();
    int pll = clock_init();
    sh_init();

    SYST_RVR = system_hz / 1000 - 1;
    SYST_CVR = 0;
    SYST_CSR = 7; // processor clock, interrupt, enable

    voice_setup();
    for (int i = 0; i < 20; i++) // charge every S&H before the master volume comes up
        sh_refresh();
    sh_set(CV_MASTER_VOL, MASTER_VOLUME);

    panel_gpio_init();
    int have_panel = panel_init() != 0;
    uint8_t all_on[PANEL_RAM_BYTES], all_off[PANEL_RAM_BYTES] = {0};
    for (int i = 0; i < PANEL_RAM_BYTES; i++)
        all_on[i] = 0xFF;

    int shown = -1;
    uint32_t last_keys = 0;
    int muted = 0;
    for (;;) {
        IWDG_KR = IWDG_KR_REFRESH;

        uint32_t t = ms_ticks % (GAP_MS + SEGMENT_COUNT * SEGMENT_MS);
        int segment = t < GAP_MS ? -1 : (int)((t - GAP_MS) / SEGMENT_MS);

        if (have_panel && ms_ticks - last_keys >= 50) {
            last_keys = ms_ticks;
            uint8_t keys[PANEL_KEY_BYTES] = {0};
            muted = 0;
            if (panel_read_keys(keys))
                for (int i = 0; i < PANEL_KEY_BYTES; i++)
                    muted |= keys[i];
        }

        if (segment < 0)
            play(-1, PART_OSC_A, 1);
        else
            play(segment / 3, (part_t)(segment % 3), muted);

        int lit = segment >= 0 || !pll;
        if (have_panel && lit != shown) {
            panel_write(lit ? all_on : all_off);
            shown = lit;
        }

        sh_refresh();
    }
}
