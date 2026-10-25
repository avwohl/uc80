/* lc_acos.c - acos */
#include "m.h"

extern double sqrt(double x);

/* ALIAS: acos = acosf acosl */
double acos(double x)
{
    m_u u;
    float y;

    u.f = x;
    if (M_SPEC(u)) {
        if (M_NANM(u))
            return x;
        u.u = M_NAN;
        return u.f;
    }
    if (x > 1.0f || x < -1.0f) {
        u.u = M_NAN;
        return u.f;
    }
    if (x > 0.5f) {
        y = _m_kasin(sqrt(0.5f * (1.0f - x)));
        return y + y;
    }
    if (x < -0.5f) {
        y = _m_kasin(sqrt(0.5f * (1.0f + x)));
        return (-2.0f * y + -8.74227766e-08f) + 3.14159274f;
    }
    y = _m_kasin(x);
    return (-y + -4.37113883e-08f) + 1.57079637f;
}
