/* lc_m_kcos.c - _m_kcos: cos(r) for |r| <= pi/4 (see m.h).
 * cos(r) = 1 - z/2 + z*z*P(z) with z = r*r. */
#include "m.h"

static const float kcos_p[] = {
    0.0416666679f, -0.00138888881f, 2.48005981e-05f, -2.73006862e-07f
};

float _m_kcos(float r)
{
    float z = r * r;

    return (1.0f - 0.5f * z) + z * z * _m_poly(z, kcos_p, 4);
}
