/* lc_log10.c - log10 */
#include "m.h"

/* ALIAS: log10 = log10f log10l */
double log10(double x)
{
    m_u u;
    int n;
    float f, y, z;

    u.f = x;
    if (M_EXP(u) == 255) {
        if (M_MANT(u) || !M_NEG(u))
            return x;
        u.u = M_NAN;
        return u.f;
    }
    if (M_ABSBITS(u) == 0) {
        u.u = 0xFF800000UL;
        return u.f;
    }
    if (M_NEG(u)) {
        u.u = M_NAN;
        return u.f;
    }
    y = _m_logk(x, &n, &f);
    /* log10(e) = 0.43359375 + 7.00731903e-4, log10(2) = 0.30078125 +
     * 2.48745664e-4; the parts with 8 bits give exact products */
    z = (f + y) * 7.00731903e-4f;
    z = z + y * 0.43359375f;
    z = z + f * 0.43359375f;
    z = z + (float)n * 2.48745664e-4f;
    return z + (float)n * 0.30078125f;
}
