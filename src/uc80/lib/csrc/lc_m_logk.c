/* lc_m_logk.c - _m_logk, the core of log, log2, log10 (see m.h).
 * log(1+f) = f - f*f/2 + f*f*f*P(f) on [sqrt(1/2) - 1, sqrt(2) - 1]. */
#include "m.h"

static const float logk_p[] = {
    0.333333313f, -0.24999994f, 0.200007141f, -0.166680574f, 0.142493218f,
    -0.124201484f, 0.116770148f, -0.115145631f, 0.0703758299f
};

float _m_logk(float x, int *np, float *fp)
{
    m_u u;
    unsigned long mant;
    int e;
    float m, f, z, y;

    mant = _m_unpack(x, &e);
    u.u = 0x3F800000UL | (mant & 0x7FFFFFUL);
    m = u.f;                            /* 1 <= m < 2 */
    e -= 127;
    if (m > 1.41421356f) {
        m = m * 0.5f;
        e++;
    }
    f = m - 1.0f;
    z = f * f;
    y = ((((((((logk_p[8] * f + logk_p[7]) * f + logk_p[6]) * f + logk_p[5])
             * f + logk_p[4]) * f + logk_p[3]) * f + logk_p[2]) * f
          + logk_p[1]) * f + logk_p[0]) * f * z;
    y = y - 0.5f * z;
    *np = e;
    *fp = f;
    return y;
}
