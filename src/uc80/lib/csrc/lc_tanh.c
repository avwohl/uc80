/* lc_tanh.c - tanh.  Up to |x| = 0.6 a kernel x + x*z*P(z); above,
 * tanh = -t/(t+2) with t = expm1(-2|x|); from |x| = 9.1 the result is 1. */
#include "m.h"

extern double expm1(double x);

static const float tanh_p[] = {
    -0.333333343f, 0.133333132f, -0.0539615527f, 0.0217883438f,
    -0.00841013808f, 0.00237083971f
};

/* ALIAS: tanh = tanhf tanhl */
double tanh(double x)
{
    m_u u;
    float ax, z, y, t;
    int neg;

    u.f = x;
    if (M_SPEC(u)) {
        if (M_NANM(u))
            return x;
        return M_NEG(u) ? -1.0f : 1.0f;
    }
    if (M_TINY(u))
        return x;
    neg = M_NEG(u) != 0;
    ax = neg ? -x : x;
    if (ax >= 9.1f) {
        y = 1.0f;
    } else if (ax < 0.6f) {
        z = ax * ax;
        y = ax + ax * z * _m_poly(z, tanh_p, 6);
    } else {
        t = expm1(-2.0f * ax);
        y = -t / (t + 2.0f);
    }
    return neg ? -y : y;
}
