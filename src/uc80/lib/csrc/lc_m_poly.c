/* lc_m_poly.c - _m_poly (see m.h): a polynomial by Horner's rule.  The
 * kernels of the math functions call it, so each of them is a table and
 * a call instead of a chain of multiplications and additions. */
#include "m.h"

float _m_poly(float x, const float *c, int n)
{
    float y = c[--n];

    while (n--)
        y = y * x + c[n];
    return y;
}
