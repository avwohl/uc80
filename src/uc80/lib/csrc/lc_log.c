/* lc_log.c - log */
#include "m.h"

/* ALIAS: log = logf logl */
double log(double x)
{
    m_u u;
    int n;
    float f, y;

    u.f = x;
    if (M_EXP(u) == 255) {
        if (M_MANT(u) || !M_NEG(u))
            return x;                   /* NaN, +inf */
        u.u = M_NAN;
        return u.f;
    }
    if (M_ABSBITS(u) == 0) {
        u.u = 0xFF800000UL;             /* log(+-0) = -inf */
        return u.f;
    }
    if (M_NEG(u)) {
        u.u = M_NAN;
        return u.f;
    }
    y = _m_logk(x, &n, &f);
    /* ln2 = 0.693359375 - 2.12194440e-4 */
    y = y + (float)n * -2.12194440e-4f;
    y = y + f;
    return y + (float)n * 0.693359375f;
}
