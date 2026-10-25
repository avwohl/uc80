/* lc_exp.c - exp */
#include "m.h"

/* ALIAS: exp = expf expl */
double exp(double x)
{
    m_u u;
    int n;

    u.f = x;
    if (M_EXP(u) == 255) {
        if (M_MANT(u))
            return x;                   /* NaN */
        return M_NEG(u) ? 0.0f : x;     /* exp(-inf) = 0, exp(+inf) = inf */
    }
    if (x > 88.7228394f) {
        u.u = M_INF;
        return u.f;
    }
    if (x < -103.972084f)
        return 0.0f;
    if (M_EXP(u) < 103)
        return 1.0f;                    /* |x| < 2^-24 */
    u.f = _m_expk(x, &n);
    return _m_pack(0, M_MANT(u) | 0x800000UL, M_EXP(u) + n);
}
