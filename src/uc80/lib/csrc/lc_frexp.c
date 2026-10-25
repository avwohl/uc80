/* lc_frexp.c - frexp */
#include "m.h"

/* ALIAS: frexp = frexpf frexpl */
double frexp(double x, int *ep)
{
    m_u u;
    unsigned long m;
    int e;

    u.f = x;
    if (M_SPEC(u) || M_ISZERO(u)) {
        *ep = 0;
        return x;
    }
    m = _m_unpack(x, &e);
    *ep = e - 126;
    u.u = (u.u & 0x80000000UL) | (126UL << 23) | (m & 0x7FFFFFUL);
    return u.f;
}
