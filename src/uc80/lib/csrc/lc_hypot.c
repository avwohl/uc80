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
    float ah, al, bh, bl, pa, pb, ea, eb, s, es, lo, r, h, hh, hl, p, pe;
    int ex, ey, k;

    a.f = x;
    b.f = y;
    a.u &= 0x7FFFFFFFUL;
    b.u &= 0x7FFFFFFFUL;
    if (a.u == M_INF || b.u == M_INF) {
        t.u = M_INF;
        return t.f;
    }
    if ((M_EXP(a) == 255) || (M_EXP(b) == 255))
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
    M_SPLIT(a.f, ah, al);
    M_SPLIT(b.f, bh, bl);
    pa = a.f * a.f;
    ea = ((ah * ah - pa) + 2.0f * ah * al) + al * al;
    pb = b.f * b.f;
    eb = ((bh * bh - pb) + 2.0f * bh * bl) + bl * bl;
    s = pa + pb;
    es = (pa - s) + pb;                 /* pa >= pb: exact */
    lo = (ea + eb) + es;
    h = sqrt(s);
    M_SPLIT(h, hh, hl);
    p = h * h;
    pe = ((hh * hh - p) + 2.0f * hh * hl) + hl * hl;
    r = ((s - p) - pe) + lo;
    h = h + r / (h + h);
    return scalbn(h, k);
}
