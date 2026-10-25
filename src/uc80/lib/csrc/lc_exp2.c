/* lc_exp2.c - exp2.  2^x = 2^n * exp(f*ln2) with f = x - n exact; the
 * product f*ln2 is split in a main part and a small correction. */
#include "m.h"

/* ALIAS: exp2 = exp2f exp2l */
double exp2(double x)
{
    m_u u;
    int n;
    float f, r, c, y, pe, z;

    u.f = x;
    if (M_SPEC(u)) {
        if (M_NANM(u))
            return x;
        return M_NEG(u) ? 0.0f : x;
    }
    if (x >= 128.0f) {
        u.u = M_INF;
        return u.f;
    }
    if (x < -150.0f)
        return 0.0f;
    if (M_TINY25(u))
        return 1.0f;
    n = (int)(x + (x < 0 ? -0.5f : 0.5f));
    f = x - (float)n;
    /* r + c = f * ln2, where ln2 = 0.693147182 - 1.90465421e-9 */
    r = _m_2prod(f, 0.693147182f, &pe);
    c = pe + f * -1.90465421e-9f;
    z = r * r;
    y = ((((0.000198577953f * r + 0.00139335997f) * r + 0.00833336078f) * r
          + 0.0416664667f) * r + 0.166666672f) * r + 0.5f;
    y = y * z + r;
    y = (y + 1.0f) + (y + 1.0f) * c;
    u.f = y;
    return _m_pack(0, M_MANT(u) | 0x800000UL, M_EXP(u) + n);
}
