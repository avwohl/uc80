/* lc_fdim.c - fdim: x - y if x > y, else +0; a NaN gives a NaN */
#include "m.h"

/* ALIAS: fdim = fdimf fdiml */
double fdim(double x, double y)
{
    m_u a, b;

    a.f = x;
    b.f = y;
    if M_ISNAN(a)
        return x;
    if M_ISNAN(b)
        return y;
    if (x > y)
        return x - y;
    return 0.0f;
}
