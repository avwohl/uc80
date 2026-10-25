/* lc_m_kasin.c - _m_kasin: asin(t) for |t| <= 1/2 (see m.h).
 * asin(t) = t + t*z*P(z) with z = t*t. */
#include "m.h"

static const float kasin_p[] = {
    0.166666657f, 0.0750009865f, 0.04459811f, 0.0311153121f, 0.017081691f,
    0.0337996557f
};

float _m_kasin(float t)
{
    float z = t * t;

    return t + t * z * _m_poly(z, kasin_p, 6);
}
