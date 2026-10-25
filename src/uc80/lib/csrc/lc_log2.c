/* lc_log2.c - log2 */
#include "m.h"

/* ALIAS: log2 = log2f log2l */
double log2(double x)
{
    m_u u;
    int n;
    float f, y, z;

    u.f = x;
    if (M_SPEC(u)) {
        if (M_NANM(u) || !M_NEG(u))
            return x;
        u.u = M_NAN;
        return u.f;
    }
    if (M_ISZERO(u)) {
        u.u = 0xFF800000UL;
        return u.f;
    }
    if (M_NEG(u)) {
        u.u = M_NAN;
        return u.f;
    }
    y = _m_logk(x, &n, &f);
    z = f + y;                          /* log(m) */
    /* log2(e) = 1 + 0.44269504 */
    return ((z * 0.44269504f + z) + (float)n);
}
