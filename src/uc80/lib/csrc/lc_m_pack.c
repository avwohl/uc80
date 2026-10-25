/* lc_m_pack.c - _m_pack, see m.h */
#include "m.h"

float _m_pack(int neg, unsigned long mant, int e)
{
    m_u u;
    unsigned long rem, half;
    int sh;

    if (mant == 0) {
        u.u = 0;
    } else if (e >= 255) {
        u.u = M_INF;
    } else if (e >= 1) {
        u.u = ((unsigned long)e << 23) + (mant - 0x800000UL);
    } else {
        sh = 1 - e;
        if (sh > 24) {
            u.u = 0;
        } else {
            rem = mant & ((1UL << sh) - 1);
            half = 1UL << (sh - 1);
            mant >>= sh;
            if (rem > half || (rem == half && (mant & 1)))
                mant++;
            u.u = mant;
        }
    }
    if (neg)
        u.u |= 0x80000000UL;
    return u.f;
}
