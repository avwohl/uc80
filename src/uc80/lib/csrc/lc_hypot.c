/* lc_hypot.c - hypot: sqrt(x*x + y*y) without overflow or underflow.
 *
 * Both arguments are scaled by a power of two so that the larger one is
 * in [1, 2).  The sum of the squares is kept as two floats (a Veltkamp
 * split gives the rounding error of each product), sqrt gives the first
 * guess, and the exact residual corrects it. */
#include "m.h"

extern double sqrt(double x);
extern double scalbn(double x, int n);

/* ALIAS: hypot = hypotf hypotl */
double hypot(double x, double y)
{
    m_u a, b, t;
    float pa, pb, ea, eb, s, es, lo, r, h, p, pe;
    int ex, ey, k;

    a.f = x;
    b.f = y;
    a.u &= 0x7FFFFFFFUL;
    b.u &= 0x7FFFFFFFUL;
    if (a.u == M_INF || b.u == M_INF) {
        t.u = M_INF;
        return t.f;
    }
    if ((M_SPEC(a)) || (M_SPEC(b)))
        return a.f + b.f;               /* a NaN */
    if (a.u < b.u) {
        t = a;
        a = b;
        b = t;
    }
    if (b.u == 0)
        return a.f;
    _m_unpack(a.f, &ex);
    _m_unpack(b.f, &ey);
    if (ex - ey > 30)
        return a.f;                     /* b is below the last bit of a */
    k = ex - 127;
    /* scale: a in [1,2), b by the same power of two */
    a.f = scalbn(a.f, -k);
    b.f = scalbn(b.f, -k);
    if (M_EXP(b) == 0)
        b.u = 0;
    pa = _m_2prod(a.f, a.f, &ea);
    pb = _m_2prod(b.f, b.f, &eb);
    s = _m_2sum(pa, pb, &es);
    lo = (ea + eb) + es;
    h = sqrt(s);
    p = _m_2prod(h, h, &pe);
    r = ((s - p) - pe) + lo;
    h = h + r / (h + h);
    return scalbn(h, k);
}
