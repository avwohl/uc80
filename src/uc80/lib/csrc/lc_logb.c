/* lc_logb.c - logb: the binary exponent of x as a floating value */
#include "m.h"

/* ALIAS: logb = logbf logbl */
double logb(double x)
{
    m_u u;
    int e;

    u.f = x;
    if (M_EXP(u) == 255) {
        if (M_MANT(u))
            return x;           /* NaN */
        u.u = M_INF;            /* logb(+-inf) is +inf */
        return u.f;
    }
    if (M_ABSBITS(u) == 0) {
        u.u = 0xFF800000UL;
        return u.f;
    }
    _m_unpack(x, &e);
    return (float)(e - 127);
}
