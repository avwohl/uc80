/* lc_modf.c - modf */
#include "m.h"

/* ALIAS: modf = modff modfl */
double modf(double x, double *iptr)
{
    m_u u, f;
    float ip;

    u.f = x;
    if (M_EXP(u) < 127) {               /* |x| < 1: the integer part is 0 */
        f.u = u.u & 0x80000000UL;
        *iptr = f.f;
        return x;
    }
    ip = _m_round(x, 0);
    *iptr = ip;
    if (M_SPEC(u))
        f.u = M_NANM(u) ? u.u : (u.u & 0x80000000UL);   /* inf: +-0 */
    else {
        f.f = x - ip;
        if (f.u << 1 == 0)
            f.u = u.u & 0x80000000UL;   /* a zero keeps the sign of x */
    }
    return f.f;
}
