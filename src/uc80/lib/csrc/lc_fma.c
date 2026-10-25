/* lc_fma.c - fma: x*y + z with one rounding (the product is exact as two
 * floats, and the sum of the three is rounded once except in rare double
 * rounding cases) */
#include "m.h"

/* ALIAS: fma = fmaf fmal */
double fma(double x, double y, double z)
{
    m_u a, b, c;
    float xh, xl, yh, yl, p, pe, s, bb, es;

    a.f = x;
    b.f = y;
    c.f = z;
    if (M_EXP(a) == 255 || M_EXP(b) == 255 || M_EXP(c) == 255)
        return x * y + z;
    if (M_ABSBITS(a) == 0 || M_ABSBITS(b) == 0)
        return x * y + z;
    p = x * y;
    c.f = p;
    if (M_EXP(c) == 255 || M_EXP(c) < 30)
        return p + z;                   /* overflow, or no room for the error */
    M_SPLIT(x, xh, xl);
    M_SPLIT(y, yh, yl);
    pe = ((xh * yh - p) + xh * yl + xl * yh) + xl * yl;
    s = p + z;
    bb = s - p;
    es = (p - (s - bb)) + (z - bb);     /* the exact error of p + z */
    return s + (es + pe);
}
