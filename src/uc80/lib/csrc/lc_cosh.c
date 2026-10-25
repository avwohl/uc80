/* lc_cosh.c - cosh = (e + 1/e)/2; for |x| > 22 exp(|x|)/2, scaled by the
 * exponent so that the result can reach FLT_MAX. */
#include "m.h"

/* ALIAS: cosh = coshf coshl */
double cosh(double x)
{
    m_u u, v;
    float ax, e;
    int n;

    u.f = x;
    if (M_EXP(u) == 255) {
        if (M_MANT(u))
            return x;
        u.u &= 0x7FFFFFFFUL;            /* cosh(+-inf) = inf */
        return u.f;
    }
    if (M_EXP(u) < 114)
        return 1.0f;
    ax = M_NEG(u) ? -x : x;
    if (ax > 100.0f) {
        v.u = M_INF;
        return v.f;
    }
    v.f = _m_expk(ax, &n);
    if (ax > 22.0f)
        return _m_pack(0, M_MANT(v) | 0x800000UL, M_EXP(v) + n - 1);
    e = _m_pack(0, M_MANT(v) | 0x800000UL, M_EXP(v) + n);
    return 0.5f * (e + 1.0f / e);
}
