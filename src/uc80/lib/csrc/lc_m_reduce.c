/* lc_m_reduce.c - _m_reduce: argument reduction for sin, cos and tan.
 *
 * For ax < 6000 the multiple of pi/2 is subtracted in four parts with 12,
 * 12, 12 and 24 bits, so each product n*part is exact and the result has
 * full relative accuracy even next to a multiple of pi/2.  Larger
 * arguments use the Payne-Hanek method: the bits of 2/pi around the
 * place of the exponent of ax are multiplied by the mantissa of ax, which
 * gives the quadrant and 102 bits of the fraction exactly.
 */
#include "m.h"

extern double scalbn(double x, int n);

/* the first 272 bits of 2/pi */
static const unsigned char red_tp[34] = {
    0xA2, 0xF9, 0x83, 0x6E, 0x4E, 0x44, 0x15, 0x29, 0xFC, 0x27, 0x57, 0xD1,
    0xF5, 0x34, 0xDD, 0xC0, 0xDB, 0x62, 0x95, 0x99, 0x3C, 0x43, 0x90, 0x41,
    0xFE, 0x51, 0x63, 0xAB, 0xDE, 0xBB, 0xC5, 0x61, 0xB7, 0x24
};

int _m_reduce(float ax, float *rp)
{
    unsigned long mant;
    int e, n, i, j, k, i0, pos, bit;
    float nf, r, df;
    unsigned char w[13], wl[13], pr[13], mb[3];
    unsigned t, carry;
    unsigned long m32;

    if (ax <= 0.785398185f) {
        *rp = ax;
        return 0;
    }
    if (ax < 6000.0f) {
        n = (int)(ax * 0.636619747f + 0.5f);
        nf = (float)n;
        r = ((ax - nf * 1.5703125f) - nf * 0.000483751297f)
            - nf * 7.54953362e-08f;
        *rp = r - nf * 2.56334407e-12f;
        return n & 3;
    }
    /* Payne-Hanek */
    mant = _m_unpack(ax, &e);
    i0 = (e - 150) - 1;                 /* the first bit of 2/pi needed */
    for (j = 0; j < 13; j++)
        w[j] = 0;
    for (j = 0; j < 104; j++) {
        i = i0 + j;
        if (i >= 1 && i <= 272) {
            bit = (red_tp[(i - 1) >> 3] >> (7 - ((i - 1) & 7))) & 1;
            if (bit)
                w[j >> 3] |= (unsigned char)(0x80 >> (j & 7));
        }
    }
    for (j = 0; j < 13; j++)
        wl[j] = w[12 - j];
    mb[0] = (unsigned char)mant;
    mb[1] = (unsigned char)(mant >> 8);
    mb[2] = (unsigned char)(mant >> 16);
    for (j = 0; j < 13; j++)
        pr[j] = 0;
    for (i = 0; i < 3; i++) {
        carry = 0;
        for (k = 0; k < 13 - i; k++) {
            t = pr[i + k] + (unsigned)mb[i] * wl[k] + carry;
            pr[i + k] = (unsigned char)t;
            carry = t >> 8;
        }
    }
    /* y = pr / 2^102 (mod 4); add 1/2 to get the nearest integer */
    pr[12] += 0x20;
    n = pr[12] >> 6;
    pr[12] &= 0x3F;
    if (pr[12] & 0x20) {
        pr[12] &= 0x1F;                 /* the fraction is >= 0: d = frac */
        bit = 0;
    } else {
        /* d = frac - 1/2 < 0: the magnitude is 2^101 - frac */
        carry = 1;
        for (j = 0; j < 13; j++) {
            t = (unsigned char)~pr[j] + carry;
            pr[j] = (unsigned char)t;
            carry = t >> 8;
        }
        pr[12] &= 0x1F;
        bit = 1;
    }
    /* find the leading one of the 101-bit magnitude */
    pos = 100;
    j = 12;
    while (j >= 0 && pr[j] == 0)
        j--;
    if (j < 0) {
        *rp = 0.0f;
        return n;
    }
    i = 7;
    while (!(pr[j] & (1 << i)))
        i--;
    pos = j * 8 + i;
    /* the top 32 bits starting at the leading one */
    m32 = 0;
    for (k = 0; k < 32; k++) {
        m32 <<= 1;
        i = pos - k;
        if (i >= 0 && (pr[i >> 3] & (1 << (i & 7))))
            m32 |= 1;
    }
    df = (float)m32;                    /* m32 * 2^(pos - 31 - 102) */
    df = scalbn(df, pos - 133);
    /* r = df * pi/2 */
    r = df * 1.57079637f;
    r = r + df * -4.37113883e-08f;
    *rp = bit ? -r : r;
    return n;
}
