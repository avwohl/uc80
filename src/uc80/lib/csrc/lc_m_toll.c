/* lc_m_toll.c - _m_toll, see m.h */
#include "m.h"

long long _m_toll(float r)
{
    m_u u;
    unsigned long long m;
    int e;

    u.f = r;
    e = M_EXP(u) - 150;
    if (M_EXP(u) < 127)
        return 0;
    if (M_EXP(u) >= 127 + 63)
        return M_NEG(u) ? (-0x7FFFFFFFFFFFFFFFLL - 1) : 0x7FFFFFFFFFFFFFFFLL;
    m = (u.u & 0x7FFFFFUL) | 0x800000UL;
    if (e >= 0)
        m <<= e;
    else
        m >>= -e;
    return M_NEG(u) ? -(long long)m : (long long)m;
}
