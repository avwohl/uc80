/* lc_tan.c - tan.  tan(r) = r + r*z*P(z), z = r*r; an odd quadrant uses
 * -1/tan(r). */
#include "m.h"

static const float tan_p[] = {
    0.333333343f, 0.13333343f, 0.0539650135f, 0.0219107103f, 0.00860909093f,
    0.00442569563f, -4.5089871e-07f, 0.00178946124f
};

/* ALIAS: tan = tanf tanl */
double tan(double x)
{
    m_u u;
    float r, z, y;
    int n, neg;

    u.f = x;
    if (M_SPEC(u)) {
        if (M_NANM(u))
            return x;
        u.u = M_NAN;
        return u.f;
    }
    if (M_TINY(u))
        return x;
    neg = M_NEG(u);
    u.u &= 0x7FFFFFFFUL;
    n = _m_reduce(u.f, &r);
    z = r * r;
    y = r + r * z * (((((((tan_p[7] * z + tan_p[6]) * z + tan_p[5]) * z
                         + tan_p[4]) * z + tan_p[3]) * z + tan_p[2]) * z
                       + tan_p[1]) * z + tan_p[0]);
    if (n & 1)
        y = -1.0f / y;
    return neg ? -y : y;
}
