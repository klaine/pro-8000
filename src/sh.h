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
// The waits per channel, in tenths of a microsecond: for the DAC to settle, then for the S&H to sample. 0 = the
// default (6 us, 12 us). A probe: not stored.
void sh_set_timing(uint32_t settle, uint32_t sample);
void sh_timing(uint32_t *settle, uint32_t *sample);
void sh_gate(gate_t gate, int on);
// Writes every CV to its S&H once. The hold capacitors droop, so this has to run continuously.
void sh_refresh(void);
// The same in parts, so the main loop can write the envelope-driven CVs more often than the rest. A channel through
// the DAC80501 (pitch, cutoff) costs about 50 us, its frame, and writes the VCA at its address alongside (and a global
// CV there that is due); one through an on-chip DAC alone (VCA, global) about 18 us.
void sh_refresh_pitch(unsigned voices);  // oscillator A and B of the voices in the bitmask
void sh_refresh_cutoff(unsigned voices);
void sh_refresh_vca(unsigned skip);      // every VCA but those in `skip` (written alongside already)
void sh_refresh_global(void);            // levels, noise, master volume, PW, resonance: those not written alongside
// Ends the sample in progress: before anything else drives the DACs or the address lines (the bus probe).
void sh_hold(void);

#endif
