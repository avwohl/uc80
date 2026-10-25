/* lc_logb.c - logb: the binary exponent of x as a floating value */
#include "m.h"

/* ALIAS: logb = logbf logbl */
double logb(double x)
{
    m_u u;
    int e;

    u.f = x;
    if (M_SPEC(u)) {
        if (M_NANM(u))
            return x;           /* NaN */
        u.u = M_INF;            /* logb(+-inf) is +inf */
        return u.f;
    }
    if (M_ISZERO(u)) {
        u.u = 0xFF800000UL;
        return u.f;
    }
    _m_unpack(x, &e);
    return (float)(e - 127);
}
