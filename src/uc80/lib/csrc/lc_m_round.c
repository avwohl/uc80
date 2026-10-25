/* lc_m_round.c - _m_round, see m.h.  floor, ceil, trunc, round and rint
 * are short calls of this function. */
#include "m.h"

float _m_round(float x, int mode)
{
    m_u u;
    unsigned long m, ipart, rem, half;
    int e, sh, neg, up;

    u.f = x;
    e = M_EXP(u) - 127;
    if (e >= 23)
        return x;               /* an integer, an infinity or a NaN */
    neg = M_NEG(u) != 0;
    if (e < 0) {
        /* |x| < 1 */
        if (M_ABSBITS(u) == 0)
            return x;
        up = 0;
        if (mode == 1)
            up = neg;
        else if (mode == 2)
            up = !neg;
        else if (mode == 3)
            up = (e == -1);
        else if (mode == 4)
            up = (e == -1 && M_MANT(u) != 0);
        u.u = up ? 0x3F800000UL : 0;
    } else {
        m = M_MANT(u) | 0x800000UL;
        sh = 23 - e;
        ipart = m >> sh;
        rem = m & ((1UL << sh) - 1);
        if (rem == 0)
            return x;
        half = 1UL << (sh - 1);
        up = 0;
        if (mode == 1)
            up = neg;
        else if (mode == 2)
            up = !neg;
        else if (mode == 3)
            up = (rem >= half);
        else if (mode == 4)
            up = (rem > half || (rem == half && (ipart & 1)));
        ipart += up;
        u.f = (float)ipart;
    }
    if (neg)
        u.u |= 0x80000000UL;
    return u.f;
}
