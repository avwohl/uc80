/* lc_sin.c - sin */
#include "m.h"

/* ALIAS: sin = sinf sinl */
double sin(double x)
{
    m_u u;
    float r, y;
    int n;

    u.f = x;
    if (M_SPEC(u)) {
        if (M_NANM(u))
            return x;
        u.u = M_NAN;                    /* sin(+-inf) */
        return u.f;
    }
    if (M_TINY(u))
        return x;                       /* |x| < 2^-13 */
    n = M_NEG(u);
    u.u &= 0x7FFFFFFFUL;
    switch (_m_reduce(u.f, &r)) {
    case 0:
        y = _m_ksin(r);
        break;
    case 1:
        y = _m_kcos(r);
        break;
    case 2:
        y = -_m_ksin(r);
        break;
    default:
        y = -_m_kcos(r);
        break;
    }
    return n ? -y : y;
}
