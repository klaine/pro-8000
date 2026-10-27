// Sample-and-hold CVs and the gate switches to the voice board.
#ifndef SH_H
#define SH_H

#include <stdint.h>

#define VOICE_COUNT 8

// CV values are 16-bit everywhere; channels fed by a 12-bit DAC use the top 12 bits.
typedef enum {
    CV_OSC_A1 = 0,                    // + voice, oscillator A pitch
    CV_OSC_B1 = CV_OSC_A1 + VOICE_COUNT, // oscillator B pitch
    CV_FIL1 = CV_OSC_B1 + VOICE_COUNT,   // filter cutoff
    CV_AMP1 = CV_FIL1 + VOICE_COUNT,     // VCA level
    CV_PMOD_OSC_B = CV_AMP1 + VOICE_COUNT,
    CV_VOL_A,
    CV_VOL_B,
    CV_MASTER_VOL,
    CV_NOISE,
    CV_EXT_FIL,
    CV_PW_A,
    CV_PW_B,
    CV_RESONANCE,
    CV_COUNT
} cv_t;

typedef enum {
    GATE_SAW_A = 0,
    GATE_TRI_A,
    GATE_SYNC,
    GATE_SAW_B,
    GATE_TRI_B,
    GATE_PMOD_FREQ_A,
    GATE_PMOD_FILTER,
    GATE_COUNT
} gate_t;

// Drives every mux enable high (all channels off). Call first thing after reset.
void sh_safe(void);
void sh_init(void);
void sh_set(cv_t cv, uint16_t value);
void sh_gate(gate_t gate, int on);
// Writes every CV to its S&H once. The hold capacitors droop, so this has to run continuously.
void sh_refresh(void);

#endif
