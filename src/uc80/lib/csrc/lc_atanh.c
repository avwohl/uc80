/* lc_atanh.c - atanh = log1p(2x/(1-x))/2 */
#include "m.h"

extern double log1p(double x);

/* ALIAS: atanh = atanhf atanhl */
double atanh(double x)
{
    m_u u;
    float ax, y;
    int neg;

    u.f = x;
    if (M_SPEC(u)) {
        if (M_NANM(u))
            return x;
        u.u = M_NAN;
        return u.f;
    }
    neg = M_NEG(u) != 0;
    ax = neg ? -x : x;
    if (ax > 1.0f) {
        u.u = M_NAN;
        return u.f;
    }
    if (ax == 1.0f) {
        u.u = M_INF | (u.u & 0x80000000UL);
        return u.f;
    }
    if (M_TINY(u))
        return x;
    if (ax < 0.5f)
        y = 0.5f * log1p(2.0f * ax + 2.0f * ax * ax / (1.0f - ax));
    else
        y = 0.5f * log1p(2.0f * ax / (1.0f - ax));
    return neg ? -y : y;
}
