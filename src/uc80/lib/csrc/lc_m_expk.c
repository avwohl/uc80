/* lc_m_expk.c - _m_expk, the core of exp, sinh and cosh (see m.h).
 * x = n*ln2 + r with |r| <= ln2/2; exp(r) = 1 + r + r*r*P(r). */
#include "m.h"

static const float expk_p[] = {
    0.5f, 0.166666672f, 0.0416664667f, 0.00833336078f, 0.00139335997f,
    0.000198577953f
};

float _m_expk(float x, int *np)
{
    int n;
    float r, y;

    n = (int)(x * 1.44269504f + (x < 0 ? -0.5f : 0.5f));
    /* 0.693359375 has 9 bits, so n * 0.693359375 is exact */
    r = (x - (float)n * 0.693359375f) - (float)n * -2.12194440e-4f;
    y = ((((expk_p[5] * r + expk_p[4]) * r + expk_p[3]) * r + expk_p[2]) * r
         + expk_p[1]) * r + expk_p[0];
    y = y * r * r + r;
    *np = n;
    return y + 1.0f;
}
