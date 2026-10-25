/* lc_scalbn.c - scalbn and ldexp: x * 2^n */
#include "m.h"

/* ALIAS: scalbn = scalbnf scalbnl ldexp ldexpf ldexpl */
double scalbn(double x, int n)
{
    m_u u;
    unsigned long m;
    int e;

    u.f = x;
    if (M_SPEC(u) || M_ISZERO(u))
        return x;
    m = _m_unpack(x, &e);
    if (n > 600)
        n = 600;
    if (n < -600)
        n = -600;
    return _m_pack(M_NEG(u) != 0, m, e + n);
}
