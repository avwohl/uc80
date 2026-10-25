/* lc_cos.c - cos */
#include "m.h"

/* ALIAS: cos = cosf cosl */
double cos(double x)
{
    m_u u;
    float r, y;

    u.f = x;
    if (M_EXP(u) == 255) {
        if (M_MANT(u))
            return x;
        u.u = M_NAN;                    /* cos(+-inf) */
        return u.f;
    }
    if (M_EXP(u) < 114)
        return 1.0f;                    /* |x| < 2^-13 */
    u.u &= 0x7FFFFFFFUL;
    switch (_m_reduce(u.f, &r)) {
    case 0:
        y = _m_kcos(r);
        break;
    case 1:
        y = -_m_ksin(r);
        break;
    case 2:
        y = -_m_kcos(r);
        break;
    default:
        y = _m_ksin(r);
        break;
    }
    return y;
}
