/* lc_cbrt.c - cbrt, correctly rounded in almost all cases.
 *
 * m (1 to 8, with an exponent that is a multiple of 3) gets z = m^(-1/3)
 * from Newton steps that need no division, y = m*z*z, and one step with
 * the exact residual y*y*y - m (Veltkamp splits) rounds y. */
#include "m.h"

/* ALIAS: cbrt = cbrtf cbrtl */
double cbrt(double x)
{
    m_u u, v;
    unsigned long mant;
    int e, q, r, i;
    float m, z, y, yh, yl, p, pe, ph, pl, q3, qe, res, w;

    u.f = x;
    if (M_EXP(u) == 255 || M_ABSBITS(u) == 0)
        return x;
    mant = _m_unpack(x, &e);
    e -= 127;
    q = e / 3;
    r = e - 3 * q;
    if (r < 0) {
        r += 3;
        q--;
    }
    v.u = 0x3F800000UL | (mant & 0x7FFFFFUL);
    m = v.f;                            /* 1 <= m < 2 */
    for (i = 0; i < r; i++)
        m = m + m;                      /* 1 <= m < 8 */
    v.f = m;
    v.u = 0x54A23000UL - v.u / 3;
    z = v.f;
    for (i = 0; i < 4; i++)
        z = z * (4.0f - m * z * z * z) * 0.33333334f;
    y = m * z * z;
    /* y*y = p + pe, p*y = q3 + qe, all exact */
    M_SPLIT(y, yh, yl);
    p = y * y;
    pe = ((yh * yh - p) + 2.0f * yh * yl) + yl * yl;
    M_SPLIT(p, ph, pl);
    q3 = p * y;
    qe = ((ph * yh - q3) + ph * yl + pl * yh) + pl * yl;
    res = ((q3 - m) + qe) + pe * y;
    w = z * z * 0.33333334f;
    y = y - res * w;
    v.f = y;
    v.u += (unsigned long)(long)q << 23;
    if (M_NEG(u))
        v.u |= 0x80000000UL;
    return v.f;
}
