/* lc_sqrt.c - sqrt, correctly rounded.
 *
 * The mantissa m (1 to 4, with an even exponent) gets 1/sqrt(m) from three
 * steps of Newton's method, y = m/sqrt(m), and one last step with the
 * exact residual m - y*y (a product with the Veltkamp split) rounds y. */
#include "m.h"

/* ALIAS: sqrt = sqrtf sqrtl */
double sqrt(double x)
{
    m_u u, v;
    unsigned long mant;
    int e, i;
    float m, rs, y, yh, yl, p, pe, r;

    u.f = x;
    if (M_ABSBITS(u) == 0)
        return x;                       /* +-0 */
    if (M_NEG(u) || M_EXP(u) == 255) {
        if (M_EXP(u) == 255 && !M_NEG(u))
            return x;                   /* +inf, and +NaN */
        v.u = M_NAN;
        return v.f;
    }
    mant = _m_unpack(x, &e);
    e -= 127;
    v.u = 0x3F800000UL | (mant & 0x7FFFFFUL);
    m = v.f;                            /* 1 <= m < 2 */
    if (e & 1) {
        m = m + m;
        e--;
    }
    /* first guess of 1/sqrt(m) by the exponent-halving trick */
    v.f = m;
    v.u = 0x5F375A86UL - (v.u >> 1);
    rs = v.f;
    for (i = 0; i < 3; i++)
        rs = rs * (1.5f - 0.5f * m * rs * rs);
    y = m * rs;
    /* y*y = p + pe exactly */
    M_SPLIT(y, yh, yl);
    p = y * y;
    pe = ((yh * yh - p) + 2.0f * yh * yl) + yl * yl;
    r = (m - p) - pe;
    y = y + r * (0.5f * rs);
    v.f = y;
    v.u += (unsigned long)(long)(e / 2) << 23;
    return v.f;
}
