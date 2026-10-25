/* lc_m_2sum.c - _m_2sum (see m.h): the sum and its exact rounding error */
#include "m.h"

float _m_2sum(float a, float b, float *e)
{
    float s = a + b;
    float bb = s - a;

    *e = (a - (s - bb)) + (b - bb);
    return s;
}
