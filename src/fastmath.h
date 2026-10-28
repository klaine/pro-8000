// Float exp2/log2 without a C library, accurate to about 1e-6 (well under a cent in pitch).
#ifndef FASTMATH_H
#define FASTMATH_H

float fast_log2(float x); // x > 0
float fast_exp2(float x); // 0 below 2^-126, at most 2^127

#endif
