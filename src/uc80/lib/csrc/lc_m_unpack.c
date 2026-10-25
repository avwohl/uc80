/* lc_m_unpack.c - _m_unpack, see m.h */
#include "m.h"

unsigned long _m_unpack(float x, int *e)
{
    m_u u;
    unsigned long m;
    int ex;

    u.f = x;
    ex = M_EXP(u);
    m = M_MANT(u);
    if (ex == 0) {
        if (m == 0) {
            *e = 0;
            return 0;
        }
        ex = 1;
        while (!(m & 0x800000UL)) {
            m <<= 1;
            ex--;
        }
    } else {
        m |= 0x800000UL;
    }
    *e = ex;
    return m;
}
