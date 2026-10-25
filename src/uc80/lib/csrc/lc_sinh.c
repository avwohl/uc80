/* lc_sinh.c - sinh.  Up to |x| = 1 a kernel x + x*z*P(z); above, through
 * expm1 (sinh = (t + t/(t+1))/2) and for |x| > 22 through exp(|x|)/2,
 * scaled by the exponent so that the result can reach FLT_MAX. */
#include "m.h"

extern double expm1(double x);

static const float sinh_p[] = {
    0.166666672f, 0.00833333936f, 0.000198381225f, 2.80614177e-06f
};

/* ALIAS: sinh = sinhf sinhl */
double sinh(double x)
{
    m_u u, v;
    float ax, z, y, t;
    int n, neg;

    u.f = x;
    if (M_EXP(u) == 255)
        return x;                       /* NaN, +-inf */
    if (M_EXP(u) < 114)
        return x;
    neg = M_NEG(u) != 0;
    ax = neg ? -x : x;
    if (ax <= 1.0f) {
        z = ax * ax;
        y = ax + ax * z * (((sinh_p[3] * z + sinh_p[2]) * z + sinh_p[1]) * z
                            + sinh_p[0]);
    } else if (ax <= 22.0f) {
        t = expm1(ax);
        y = 0.5f * (t + t / (t + 1.0f));
    } else if (ax > 100.0f) {
        v.u = M_INF;
        y = v.f;
    } else {
        v.f = _m_expk(ax, &n);
        y = _m_pack(0, M_MANT(v) | 0x800000UL, M_EXP(v) + n - 1);
    }
    return neg ? -y : y;
}
