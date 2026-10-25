/* lc_remquo.c - remquo: the IEEE remainder x - n*y, where n is x/y rounded
 * to the nearest integer (ties to even); *quo gets the sign of x/y and the
 * low 15 bits of |n|.  The result is exact. */
#include "m.h"

/* ALIAS: remquo = remquof remquol */
double remquo(double x, double y, int *quo)
{
    m_u a, b, r;
    unsigned long mx, my, rem, q;
    int ex, ey, d, flip, neg;

    a.f = x;
    b.f = y;
    *quo = 0;
    if ((M_EXP(a) == 255 && M_MANT(a)) || (M_EXP(b) == 255 && M_MANT(b))) {
        r.u = M_NAN;
        return r.f;
    }
    if (M_EXP(a) == 255 || M_ABSBITS(b) == 0) {
        r.u = M_NAN;                    /* x is inf, or y is 0 */
        return r.f;
    }
    if (M_EXP(b) == 255 || M_ABSBITS(a) == 0)
        return x;                       /* y is inf, or x is 0 */
    neg = M_NEG(a) != 0;
    mx = _m_unpack(x, &ex);
    my = _m_unpack(y, &ey);
    q = 0;
    flip = 0;
    if (ex < ey - 1)
        return x;                       /* |x| < |y|/2 */
    if (ex == ey - 1) {
        if (mx <= my)
            return x;                   /* |x| <= |y|/2: n = 0 */
        rem = 2 * my - mx;              /* n = 1, in units of 2^(ey-151) */
        ey--;
        q = 1;
        flip = 1;
    } else {
        rem = mx;
        for (d = ex - ey; d > 0; d--) {
            q <<= 1;
            if (rem >= my) {
                rem -= my;
                q |= 1;
            }
            rem <<= 1;
        }
        q <<= 1;
        if (rem >= my) {
            rem -= my;
            q |= 1;
        }
        if (2 * rem > my || (2 * rem == my && (q & 1))) {
            rem = my - rem;
            q++;
            flip = 1;
        }
    }
    *quo = (int)(q & 0x7FFF);
    if (M_NEG(a) != M_NEG(b))
        *quo = -*quo;
    neg ^= flip;
    if (rem == 0) {
        r.u = a.u & 0x80000000UL;       /* a zero has the sign of x */
        return r.f;
    }
    while (!(rem & 0x800000UL)) {
        rem <<= 1;
        ey--;
    }
    return _m_pack(neg, rem, ey);
}
