/* lc_m_ksin.c - _m_ksin: sin(r) for |r| <= pi/4 (see m.h).
 * sin(r) = r + r*z*P(z) with z = r*r. */
#include "m.h"

static const float ksin_p[] = {
    -0.166666672f, 0.00833333191f, -0.000198400827f, 2.72494117e-06f
};

float _m_ksin(float r)
{
    float z = r * r;

    return r + r * z * (((ksin_p[3] * z + ksin_p[2]) * z + ksin_p[1]) * z
                        + ksin_p[0]);
}
