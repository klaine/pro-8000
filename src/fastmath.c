#include "fastmath.h"
#include <stdint.h>

// Split off the exponent, then 2·atanh series on the mantissa, centred on [√½, √2).
float fast_log2(float x)
{
    union { float f; uint32_t u; } v = {.f = x};
    int exponent = (int)((v.u >> 23) & 0xFF) - 127;
    v.u = (v.u & 0x007FFFFFu) | 0x3F800000u; // mantissa in [1, 2)
    float m = v.f;
    if (m > 1.41421356f) {
        m *= 0.5f;
        exponent++;
    }
    float z = (m - 1.0f) / (m + 1.0f), z2 = z * z;
    float series = z * (1.0f + z2 * (1.0f / 3 + z2 * (1.0f / 5 + z2 * (1.0f / 7 + z2 * (1.0f / 9)))));
    return (float)exponent + 2.0f * series * 1.44269504f;
}

// Integer part into the exponent bits, fraction (in [-0.5, 0.5]) by a Taylor series of e^(f·ln 2). Outside
// -126..127 the exponent field would wrap (-128 gave -inf, -166 a huge negative), so below it's 0 and above 2^127:
// an envelope's step over a long pass, dt × rate, can reach -166.
float fast_exp2(float x)
{
    if (x < -126.0f)
        return 0;
    if (x > 127.0f)
        x = 127.0f;
    int whole = (int)(x >= 0 ? x + 0.5f : x - 0.5f);
    float f = (x - (float)whole) * 0.69314718f;
    float frac = 1.0f + f * (1.0f + f * (0.5f + f * (1.0f / 6 + f * (1.0f / 24 + f * (1.0f / 120 + f * (1.0f / 720))))));
    union { float f; uint32_t u; } v = {.u = (uint32_t)(whole + 127) << 23};
    return v.f * frac;
}
