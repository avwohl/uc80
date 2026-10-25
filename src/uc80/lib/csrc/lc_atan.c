/* lc_atan.c - atan.
 *
 * atan(x) for x >= 0 is reduced to the range [0, tan(pi/8)] with
 * atan(x) = pi/2 - atan(1/x) and atan(x) = pi/4 + atan((x-1)/(x+1)); there
 * atan(t) = t + t*z*P(z), z = t*t.  pi/2 and pi/4 are added as a high and
 * a low part. */
#include "m.h"

static const float atan_p[] = {
    -0.333333343f, 0.199999779f, -0.142841667f, 0.110716112f, -0.0862630531f,
    0.0505191945f
};

/* ALIAS: atan = atanf atanl */
double atan(double x)
{
    m_u u;
    float ax, t, z, y, base_hi, base_lo;

    u.f = x;
    if (M_SPEC(u)) {
        if (M_NANM(u))
            return x;
        ax = 1.57079637f;               /* atan(+-inf) = +-pi/2 */
        return M_NEG(u) ? -ax : ax;
    }
    if (M_TINY(u))
        return x;                       /* |x| < 2^-13 */
    ax = M_NEG(u) ? -x : x;
    if (ax > 2.41421356f) {
        t = -1.0f / ax;
        base_hi = 1.57079637f;
        base_lo = -4.37113883e-08f;
    } else if (ax > 0.414213562f) {
        t = (ax - 1.0f) / (ax + 1.0f);
        base_hi = 0.785398185f;
        base_lo = -2.18556941e-08f;
    } else {
        t = ax;
        base_hi = 0.0f;
        base_lo = 0.0f;
    }
    z = t * t;
    y = t + t * z * (((((atan_p[5] * z + atan_p[4]) * z + atan_p[3]) * z
                       + atan_p[2]) * z + atan_p[1]) * z + atan_p[0]);
    y = (y + base_lo) + base_hi;
    return M_NEG(u) ? -y : y;
}
