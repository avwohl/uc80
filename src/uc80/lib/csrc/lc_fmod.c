/* lc_fmod.c - fmod: x - n*y for the integer n that truncates x/y.
 * The result is exact: the mantissa of x is reduced by y one bit at a
 * time. */
#include "m.h"

/* ALIAS: fmod = fmodf fmodl */
double fmod(double x, double y)
{
    m_u a, b, r;
    unsigned long mx, my;
    int ex, ey;

    a.f = x;
    b.f = y;
    if (M_EXP(b) == 255 && M_MANT(b))
        return y;                       /* y is a NaN */
    if (M_EXP(a) == 255) {
        r.u = M_NAN;                    /* x is inf or NaN */
        return M_MANT(a) ? x : r.f;
    }
    if (M_ABSBITS(b) == 0) {
        r.u = M_NAN;                    /* y is 0 */
        return r.f;
    }
    if (M_EXP(b) == 255 || M_ABSBITS(a) == 0)
        return x;                       /* y is inf, or x is 0 */
    mx = _m_unpack(x, &ex);
    my = _m_unpack(y, &ey);
    if (ex < ey)
        return x;
    while (ex > ey) {
        if (mx >= my)
            mx -= my;
        mx <<= 1;
        ex--;
    }
    if (mx >= my)
        mx -= my;
    if (mx == 0) {
        r.u = a.u & 0x80000000UL;
        return r.f;
    }
    while (!(mx & 0x800000UL)) {
        mx <<= 1;
        ex--;
    }
    return _m_pack(M_NEG(a) != 0, mx, ex);
}
