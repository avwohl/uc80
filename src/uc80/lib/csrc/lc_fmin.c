/* lc_fmin.c - fmin: the smaller value; a NaN argument is ignored,
 * and fmin(-0, +0) is -0 */
#include "m.h"

/* ALIAS: fmin = fminf fminl */
double fmin(double x, double y)
{
    m_u a, b;

    a.f = x;
    b.f = y;
    if (M_EXP(a) == 255 && M_MANT(a))
        return y;
    if (M_EXP(b) == 255 && M_MANT(b))
        return x;
    if (x < y)
        return x;
    if (y < x)
        return y;
    return M_NEG(a) ? x : y;
}
