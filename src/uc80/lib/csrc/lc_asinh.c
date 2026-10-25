/* lc_asinh.c - asinh = log(|x| + sqrt(x*x + 1)), in a form that is exact
 * for small x: log1p(|x| + x*x/(1 + sqrt(1 + x*x))) */
#include "m.h"

extern double log(double x);
extern double log1p(double x);
extern double sqrt(double x);

/* ALIAS: asinh = asinhf asinhl */
double asinh(double x)
{
    m_u u;
    float ax, t, y;
    int neg;

    u.f = x;
    if (M_SPEC(u))
        return x;
    if (M_TINY(u))
        return x;
    neg = M_NEG(u) != 0;
    ax = neg ? -x : x;
    if (ax > 16777216.0f) {
        y = log(ax) + 0.693147182f;
    } else {
        t = ax * ax;
        y = log1p(ax + t / (1.0f + sqrt(1.0f + t)));
    }
    return neg ? -y : y;
}
