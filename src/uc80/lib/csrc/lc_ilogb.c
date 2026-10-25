/* lc_ilogb.c - ilogb: the binary exponent of x as an int.
 * 0 and NaN give FP_ILOGB0 and FP_ILOGBNAN (INT_MIN), infinity INT_MAX. */
#include "m.h"

/* ALIAS: ilogb = ilogbf ilogbl */
int ilogb(double x)
{
    m_u u;
    int e;

    u.f = x;
    if (M_SPEC(u))
        return M_NANM(u) ? (-32767 - 1) : 32767;
    if (M_ISZERO(u))
        return -32767 - 1;
    _m_unpack(x, &e);
    return e - 127;
}
