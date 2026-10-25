/* lc_fdim.c - fdim: x - y if x > y, else +0; a NaN gives a NaN */
#include "m.h"

/* ALIAS: fdim = fdimf fdiml */
double fdim(double x, double y)
{
    m_u a, b;

    a.f = x;
    b.f = y;
    if (M_EXP(a) == 255 && M_MANT(a))
        return x;
    if (M_EXP(b) == 255 && M_MANT(b))
        return y;
    if (x > y)
        return x - y;
    return 0.0f;
}
