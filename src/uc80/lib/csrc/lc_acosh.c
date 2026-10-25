/* lc_acosh.c - acosh */
#include "m.h"

extern double log(double x);
extern double log1p(double x);
extern double sqrt(double x);

/* ALIAS: acosh = acoshf acoshl */
double acosh(double x)
{
    m_u u;
    float t;

    u.f = x;
    if (M_SPEC(u)) {
        if (M_NANM(u) || !M_NEG(u))
            return x;
        u.u = M_NAN;
        return u.f;
    }
    if (x < 1.0f) {
        u.u = M_NAN;
        return u.f;
    }
    if (x > 16777216.0f)
        return log(x) + 0.693147182f;
    if (x > 2.0f)
        return log(2.0f * x - 1.0f / (x + sqrt(x * x - 1.0f)));
    t = x - 1.0f;
    return log1p(t + sqrt(2.0f * t + t * t));
}
