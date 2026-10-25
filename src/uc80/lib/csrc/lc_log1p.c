/* lc_log1p.c - log1p: log(1+x), accurate for small x (Kahan's formula) */
#include "m.h"

extern double log(double x);

/* ALIAS: log1p = log1pf log1pl */
double log1p(double x)
{
    m_u a;
    float u;

    a.f = x;
    if (M_SPEC(a)) {
        if (M_NANM(a) || !M_NEG(a))
            return x;
        a.u = M_NAN;
        return a.f;
    }
    if (x < -1.0f) {
        a.u = M_NAN;
        return a.f;
    }
    if (x == -1.0f) {
        a.u = 0xFF800000UL;
        return a.f;
    }
    if (M_TINY25(a))
        return x;                       /* |x| < 2^-24 */
    u = 1.0f + x;
    if (u == 1.0f)
        return x;
    return log(u) * (x / (u - 1.0f));
}
