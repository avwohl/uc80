/* lc_exp2.c - exp2.  2^x = 2^n * exp(f*ln2) with f = x - n exact; the
 * product f*ln2 is split in a main part and a small correction. */
#include "m.h"

/* ALIAS: exp2 = exp2f exp2l */
double exp2(double x)
{
    m_u u;
    int n;
    float f, r, c, y, fh, fl, pe, z;

    u.f = x;
    if (M_EXP(u) == 255) {
        if (M_MANT(u))
            return x;
        return M_NEG(u) ? 0.0f : x;
    }
    if (x >= 128.0f) {
        u.u = M_INF;
        return u.f;
    }
    if (x < -150.0f)
        return 0.0f;
    if (M_EXP(u) < 103)
        return 1.0f;
    n = (int)(x + (x < 0 ? -0.5f : 0.5f));
    f = x - (float)n;
    /* r + c = f * ln2, where ln2 = 0.693147182 - 1.90465421e-9; the
     * rounding error of the float product comes from the split of both
     * factors (ln2h + ln2l = 0.693147182) */
    r = f * 0.693147182f;
    M_SPLIT(f, fh, fl);
    pe = ((fh * 0.693115234f - r) + fh * 3.19480896e-05f + fl * 0.693115234f)
         + fl * 3.19480896e-05f;
    c = pe + f * -1.90465421e-9f;
    z = r * r;
    y = ((((0.000198577953f * r + 0.00139335997f) * r + 0.00833336078f) * r
          + 0.0416664667f) * r + 0.166666672f) * r + 0.5f;
    y = y * z + r;
    y = (y + 1.0f) + (y + 1.0f) * c;
    u.f = y;
    return _m_pack(0, M_MANT(u) | 0x800000UL, M_EXP(u) + n);
}
