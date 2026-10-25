/* lc_fma.c - fma: x*y + z with one rounding (the product is exact as two
 * floats, and the sum of the three is rounded once except in rare double
 * rounding cases) */
#include "m.h"

/* ALIAS: fma = fmaf fmal */
double fma(double x, double y, double z)
{
    m_u a, b, c;
    float p, pe, s, es;

    a.f = x;
    b.f = y;
    c.f = z;
    if (M_SPEC(a) || M_SPEC(b) || M_SPEC(c))
        return x * y + z;
    if (M_ISZERO(a) || M_ISZERO(b))
        return x * y + z;
    p = x * y;
    c.f = p;
    if (M_SPEC(c) || M_EXP(c) < 30)
        return p + z;                   /* overflow, or no room for the error */
    p = _m_2prod(x, y, &pe);
    s = _m_2sum(p, z, &es);             /* the exact error of p + z */
    return s + (es + pe);
}
