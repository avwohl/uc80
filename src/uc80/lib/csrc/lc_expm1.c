/* lc_expm1.c - expm1: exp(x) - 1 without the loss near 0 */
#include "m.h"

static const float expm1_p[] = {
    0.5f, 0.166666672f, 0.0416664667f, 0.00833336078f, 0.00139335997f,
    0.000198577953f
};

/* ALIAS: expm1 = expm1f expm1l */
double expm1(double x)
{
    m_u u;
    int n;
    float r, y, e;

    u.f = x;
    if (M_EXP(u) == 255) {
        if (M_MANT(u))
            return x;
        return M_NEG(u) ? -1.0f : x;
    }
    if (x > 88.7228394f) {
        u.u = M_INF;
        return u.f;
    }
    if (x < -17.4f)
        return -1.0f;
    if (M_EXP(u) < 103)
        return x;                       /* |x| < 2^-24 */
    if (x > -0.34657f && x < 0.34657f) {
        n = 0;
        r = x;
    } else {
        n = (int)(x * 1.44269504f + (x < 0 ? -0.5f : 0.5f));
        r = (x - (float)n * 0.693359375f) - (float)n * -2.12194440e-4f;
    }
    y = ((((expm1_p[5] * r + expm1_p[4]) * r + expm1_p[3]) * r + expm1_p[2]) * r
         + expm1_p[1]) * r + expm1_p[0];
    y = y * r * r + r;                  /* exp(r) - 1 */
    if (n == 0)
        return y;
    u.f = y + 1.0f;
    e = _m_pack(0, M_MANT(u) | 0x800000UL, M_EXP(u) + n);
    return e - 1.0f;
}
