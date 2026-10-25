/* lc_m_2prod.c - _m_2prod (see m.h): the product and its exact rounding
 * error (Dekker, with the Veltkamp split) */
#include "m.h"

float _m_2prod(float a, float b, float *e)
{
    float ah, al, bh, bl;
    float p = a * b;

    M_SPLIT(a, ah, al);
    M_SPLIT(b, bh, bl);
    *e = ((ah * bh - p) + ah * bl + al * bh) + al * bl;
    return p;
}
