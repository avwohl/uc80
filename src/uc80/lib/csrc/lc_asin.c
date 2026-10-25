/* lc_asin.c - asin.  For |x| <= 1/2 the kernel gives asin(x).  Above that,
 * asin(x) = pi/2 - 2*asin(sqrt((1-x)/2)). */
#include "m.h"

extern double sqrt(double x);

/* ALIAS: asin = asinf asinl */
double asin(double x)
{
    m_u u;
    float ax, y;

    u.f = x;
    if (M_SPEC(u)) {
        if (M_NANM(u))
            return x;
        u.u = M_NAN;
        return u.f;
    }
    ax = M_NEG(u) ? -x : x;
    if (ax > 1.0f) {
        u.u = M_NAN;
        return u.f;
    }
    if (M_TINY(u))
        return x;
    if (ax > 0.5f) {
        y = _m_kasin(sqrt(0.5f * (1.0f - ax)));
        y = (-2.0f * y + -4.37113883e-08f) + 1.57079637f;
    } else {
        y = _m_kasin(ax);
    }
    return M_NEG(u) ? -y : y;
}
