// Second hardware test: voice 1 plays a sawtooth on oscillator A while its pitch CV steps through 14 values,
// one second each, after a one-second gap. The panel lights during the steps and goes dark in the gap, so a
// recording can be lined up with the steps. Holding any panel button mutes the voice.
#include "panel.h"
#include "regs.h"
#include "sh.h"

#define GAP_MS      1000
#define STEP_MS     1000
#define STEP_COUNT  14
#define STEP_FIRST  0x2000u
#define STEP_SIZE   0x1000u

#define MASTER_VOLUME 0x6000u // about 37%, kept low on purpose
#define MIXER_A_LEVEL 0xC000u
#define FILTER_OPEN   0xE000u

static volatile uint32_t ms_ticks;

void SysTick_Handler(void) { ms_ticks++; }

static void voice_setup(void)
{
    for (int v = 0; v < VOICE_COUNT; v++) {
        sh_set(CV_OSC_A1 + v, 0x8000u);
        sh_set(CV_OSC_B1 + v, 0x8000u);
        sh_set(CV_FIL1 + v, FILTER_OPEN);
        sh_set(CV_AMP1 + v, 0);
    }
    sh_set(CV_PMOD_OSC_B, 0);
    sh_set(CV_VOL_A, MIXER_A_LEVEL);
    sh_set(CV_VOL_B, 0);
    sh_set(CV_NOISE, 0);
    sh_set(CV_EXT_FIL, 0);
    sh_set(CV_RESONANCE, 0);
    // as in p600fw with the square off: PW A at maximum, PW B at minimum, so no pulse bleeds through
    sh_set(CV_PW_A, 0xFFFFu);
    sh_set(CV_PW_B, 0);
    sh_set(CV_MASTER_VOL, 0);
    sh_gate(GATE_SAW_A, 1);
}

int main(void)
{
    sh_init();

    SYST_RVR = HSI_HZ / 1000 - 1;
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

        uint32_t t = ms_ticks % (GAP_MS + STEP_COUNT * STEP_MS);
        int step = t < GAP_MS ? -1 : (int)((t - GAP_MS) / STEP_MS);

        if (have_panel && ms_ticks - last_keys >= 50) {
            last_keys = ms_ticks;
            uint8_t keys[PANEL_KEY_BYTES] = {0};
            muted = 0;
            if (panel_read_keys(keys))
                for (int i = 0; i < PANEL_KEY_BYTES; i++)
                    muted |= keys[i];
        }

        sh_set(CV_OSC_A1, (uint16_t)(STEP_FIRST + STEP_SIZE * (uint32_t)(step < 0 ? 0 : step)));
        sh_set(CV_AMP1, step < 0 || muted ? 0 : 0xFFFFu);

        int lit = step >= 0;
        if (have_panel && lit != shown) {
            panel_write(lit ? all_on : all_off);
            shown = lit;
        }

        sh_refresh();
    }
}
