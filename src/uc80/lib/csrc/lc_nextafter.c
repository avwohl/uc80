/* lc_nextafter.c - nextafter: the float after x in the direction of y */
#include "m.h"

/* ALIAS: nextafter = nextafterf nextafterl nexttoward nexttowardf nexttowardl */
double nextafter(double x, double y)
{
    m_u a, b;
    long ka, kb;

    a.f = x;
    b.f = y;
    if (M_ISNAN(a) || M_ISNAN(b)) {
        a.u = M_NAN;
        return a.f;
    }
    /* order keys: the keys of -0 and +0 are equal */
    ka = M_NEG(a) ? -(long)M_ABSBITS(a) : (long)M_ABSBITS(a);
    kb = M_NEG(b) ? -(long)M_ABSBITS(b) : (long)M_ABSBITS(b);
    if (ka == kb)
        return y;
    if (ka == 0) {
        a.u = (kb > 0) ? 1UL : 0x80000001UL;
        return a.f;
    }
    if ((kb > ka) == (ka > 0))
        a.u++;
    else
        a.u--;
    return a.f;
}
