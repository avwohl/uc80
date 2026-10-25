/* lc_m_reduce.c - _m_reduce: argument reduction for sin, cos and tan.
 *
 * For ax < 6000 the multiple of pi/2 is subtracted in four parts with 12,
 * 12, 12 and 24 bits, so each product n*part is exact and the result has
 * full relative accuracy even next to a multiple of pi/2.  Larger
 * arguments use the Payne-Hanek method in lc_m_phred (assembly): the bits
 * of 2/pi around the place of the exponent of ax are multiplied by the
 * mantissa of ax, which gives the quadrant and 102 bits of the fraction
 * exactly.
 */
#include "m.h"

int _m_reduce(float ax, float *rp)
{
    int n;
    float nf, r;

    if (ax <= 0.785398185f) {
        *rp = ax;
        return 0;
    }
    if (ax >= 6000.0f)
        return _m_phred(ax, rp);
    n = (int)(ax * 0.636619747f + 0.5f);
    nf = (float)n;
    r = ((ax - nf * 1.5703125f) - nf * 0.000483751297f)
        - nf * 7.54953362e-08f;
    *rp = r - nf * 2.56334407e-12f;
    return n & 3;
}
