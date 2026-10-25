/* lc_atan2.c - atan2(y, x): the angle of the point (x, y), -pi to pi.
 * The special cases follow Annex F of the C standard. */
#include "m.h"

extern double atan(double x);
extern double scalbn(double x, int n);

/* ALIAS: atan2 = atan2f atan2l */
double atan2(double y, double x)
{
    m_u a, b, r;
    float ax, ay, t, v;
    int xneg, yneg;

    a.f = y;
    b.f = x;
    if ((M_EXP(a) == 255 && M_MANT(a)) || (M_EXP(b) == 255 && M_MANT(b)))
        return x + y;                   /* a NaN */
    /* no subnormal arithmetic: scale two small arguments up, and take a
     * subnormal next to a large one as zero */
    if (M_EXP(a) < 100 && M_EXP(b) < 100) {
        y = scalbn(y, 100);
        x = scalbn(x, 100);
    } else {
        if (M_EXP(a) == 0)
            y = M_NEG(a) ? -0.0f : 0.0f;
        if (M_EXP(b) == 0)
            x = M_NEG(b) ? -0.0f : 0.0f;
    }
    a.f = y;
    b.f = x;
    yneg = M_NEG(a) != 0;
    xneg = M_NEG(b) != 0;
    ay = yneg ? -y : y;
    ax = xneg ? -x : x;
    if (M_ABSBITS(a) == 0) {
        v = xneg ? 3.14159274f : 0.0f;  /* y = +-0: +-0 or +-pi */
        return yneg ? -v : v;
    }
    if (M_EXP(a) == 255) {
        if (M_EXP(b) == 255)
            v = xneg ? 2.35619450f : 0.785398185f;  /* 3pi/4, pi/4 */
        else
            v = 1.57079637f;
        return yneg ? -v : v;
    }
    if (M_ABSBITS(b) == 0) {
        v = 1.57079637f;                /* x = +-0 */
        return yneg ? -v : v;
    }
    if (M_EXP(b) == 255) {
        v = xneg ? 3.14159274f : 0.0f;  /* y finite */
        return yneg ? -v : v;
    }
    if (ay > ax) {
        t = atan(ax / ay);
        /* pi/2 - t, then for x < 0: pi/2 + t */
        if (xneg)
            v = (t + 4.37113883e-08f) + 1.57079637f;
        else
            v = (-t + -4.37113883e-08f) + 1.57079637f;
    } else {
        t = atan(ay / ax);
        if (xneg)
            v = (-t + -8.74227766e-08f) + 3.14159274f;
        else
            v = t;
    }
    return yneg ? -v : v;
}
