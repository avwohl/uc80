/* lc_pow.c - pow(x, y) = 2^(y * log2(x)).
 *
 * log2(x) is computed as two floats (about 36 bits): for x close to 1 from
 * the series of log(1+f), else from a table of log2 of 32 values c and the
 * series of log(1+u), u = x/c - 1 (u is exact with the Veltkamp split).
 * The product y*log2(x) and the exponential are also kept in two floats,
 * so the result is correctly rounded in nearly all cases; it is exact
 * when the true result is a float (pow(3, 2) is 9).
 *
 * The special cases follow Annex F of the C standard.
 */
#include "m.h"

static const float pow_th[32] = {
    0.0223678108f,
    0.0660892203f,
    0.108524427f,
    0.149747148f,
    0.189824566f,
    0.228818655f,
    0.266786546f,
    0.303780705f,
    0.339849979f,
    0.375039488f,
    0.409390897f,
    0.442943513f,
    0.475733429f,
    0.507794619f,
    0.539158821f,
    0.569855571f,
    0.599912882f,
    0.629356623f,
    0.658211529f,
    0.68650049f,
    0.714245439f,
    0.741467059f,
    0.768184364f,
    0.794415832f,
    0.820178986f,
    0.845490098f,
    0.870364666f,
    0.89481771f,
    0.918863297f,
    0.942514539f,
    0.965784192f,
    0.988684714f
};
static const float pow_tl[32] = {
    8.54366133e-10f,
    3.70311404e-09f,
    3.08798609e-09f,
    -1.77919457e-09f,
    3.38385386e-09f,
    5.47867252e-10f,
    -3.47654461e-09f,
    7.16280324e-09f,
    1.34503297e-08f,
    -2.9856595e-09f,
    1.13630367e-08f,
    -1.21702159e-08f,
    -1.30068578e-08f,
    -1.46854608e-08f,
    -8.65431105e-09f,
    -2.42684681e-08f,
    4.67520911e-09f,
    -1.27269872e-09f,
    -2.36616717e-08f,
    2.40353124e-08f,
    2.40021869e-08f,
    -2.65745612e-08f,
    2.27411832e-08f,
    -1.49726509e-10f,
    -1.78060571e-08f,
    1.87819129e-08f,
    -1.76132673e-08f,
    -2.05136406e-08f,
    1.22845745e-09f,
    2.03188648e-08f,
    2.40524844e-08f,
    -2.16938609e-08f
};

/* the series of log(1+f) after f - f*f/2, of log(1+u) - u, and exp(r) */
static const float pow_ls[] = {
    0.333333343f, -0.25f, 0.200000003f, -0.166666672f, 0.142857149f, -0.125f
};
static const float pow_lq[] = {
    -0.5f, 0.333333343f, -0.25f, 0.200000003f, -0.166666672f, 0.142857149f
};
static const float pow_ep[] = {
    0.5f, 0.166666672f, 0.0416664667f, 0.00833336078f, 0.00139335997f,
    0.000198577953f
};

/* 1 if y is an even integer, 2 if odd, 0 if not an integer (y finite) */
static int pow_isint(m_u b)
{
    int e;
    unsigned long m, low;

    e = M_EXP(b) - 127;
    if (M_EXP(b) == 0 || e < 0)
        return 0;
    if (e >= 24)
        return 1;
    m = M_MANT(b) | 0x800000UL;
    low = (e < 23) ? ((1UL << (23 - e)) - 1) : 0;
    if (m & low)
        return 0;
    return ((m >> (23 - e)) & 1) ? 2 : 1;
}

/* log2(x) as *lo + the return value, for finite x > 0 */
static float pow_log2(float x, float *lo)
{
    m_u u;
    unsigned long mant;
    int e, n, j;
    float m, f, z1, z2, c, sh, se, s_lo, ph, pl, low, lh, ll, th, tl;
    float a_h, a_l, b_h, b_l, uh, q, r, p, pe, hi;

    mant = _m_unpack(x, &e);
    n = e - 127;
    u.u = 0x3F800000UL | (mant & 0x7FFFFFUL);
    m = u.f;                            /* 1 <= m < 2 */
    f = (m > 1.41421356f) ? m * 0.5f - 1.0f : m - 1.0f;
    th = 0.0f;
    tl = 0.0f;
    if (f > -0.03125f && f < 0.03125f) {
        /* near 1: ln(1+f) = f - f*f/2 + f*f*f*(1/3 - f/4 + ...) */
        if (m > 1.41421356f)
            n++;
        z1 = _m_2prod(f, f, &z2);
        sh = _m_2sum(f, -0.5f * z1, &se);
        c = f * z1 * _m_poly(f, pow_ls, 6);
        s_lo = se + (c - 0.5f * z2);
    } else {
        j = (int)((mant >> 18) & 31);
        c = 1.0f + ((float)j + 0.5f) * 0.03125f;
        r = 1.0f / c;
        p = _m_2prod(m, r, &pe);
        sh = p - 1.0f;                  /* u = sh + pe */
        uh = sh;
        q = uh * uh * _m_poly(uh, pow_lq, 6);
        s_lo = (pe - pe * uh) + q;
        th = pow_th[j];
        tl = pow_tl[j];
    }
    /* (sh + s_lo) * log2(e), log2(e) = 1.44269502 + 1.92596303e-8 */
    ph = _m_2prod(sh, 1.44269502f, &pl);
    low = pl + sh * 1.92596303e-8f + s_lo * 1.44269502f;
    lh = ph + low;
    ll = low - (lh - ph);
    /* + n + table */
    a_h = _m_2sum((float)n, th, &a_l);
    b_h = _m_2sum(a_h, lh, &b_l);
    low = ((a_l + b_l) + tl) + ll;
    hi = b_h + low;
    *lo = low - (hi - b_h);
    return hi;
}

/* ALIAS: pow = powf powl */
double pow(double x, double y)
{
    m_u a, b, r;
    int yint, neg, n;
    float lh, ll, ph, pl, th, tl, fh, f, fl, rh, re, rl, z, p, tail, s1, e1;

    a.f = x;
    b.f = y;
    if (M_ISZERO(b) || a.u == 0x3F800000UL)
        return 1.0f;
    if (M_ISNAN(a) || M_ISNAN(b))
        return x + y;                   /* a NaN */
    if (M_SPEC(b)) {
        /* y = +-inf */
        r.u = a.u & 0x7FFFFFFFUL;
        if (r.u == 0x3F800000UL)
            return 1.0f;
        if ((r.f > 1.0f) == (M_NEG(b) == 0)) {
            r.u = M_INF;
            return r.f;
        }
        return 0.0f;
    }
    yint = pow_isint(b);
    neg = 0;
    if (M_SPEC(a) || M_ISZERO(a)) {
        /* x = +-inf or +-0 */
        neg = M_NEG(a) && yint == 2;
        if ((M_SPEC(a)) == (M_NEG(b) == 0))
            r.u = M_INF;
        else
            r.u = 0;
        if (neg)
            r.u |= 0x80000000UL;
        return r.f;
    }
    if (M_NEG(a)) {
        if (!yint) {
            r.u = M_NAN;
            return r.f;
        }
        neg = (yint == 2);
        a.u &= 0x7FFFFFFFUL;
    }
    if (a.u == 0x3F800000UL) {          /* x = -1 */
        r.u = 0x3F800000UL;
        goto sign;
    }
    if (M_EXP(b) > 127 + 90) {
        /* |y| > 2^90: the result is inf or 0 (|x| is not 1) */
        r.u = ((a.f > 1.0f) == (M_NEG(b) == 0)) ? M_INF : 0;
        goto sign;
    }
    /* t = y * log2(x) */
    lh = pow_log2(a.f, &ll);
    ph = _m_2prod(y, lh, &pl);
    pl = pl + y * ll;
    if (ph > 130.0f) {
        r.u = M_INF;
        goto sign;
    }
    if (ph < -160.0f) {
        r.u = 0;
        goto sign;
    }
    th = ph + pl;
    tl = pl - (th - ph);
    n = (int)(th + (th < 0.0f ? -0.5f : 0.5f));
    fh = th - (float)n;
    f = _m_2sum(fh, tl, &fl);
    /* r = f * ln2, ln2 = 0.693147182 - 1.90465421e-9 */
    rh = _m_2prod(f, 0.693147182f, &re);
    rl = re + f * -1.90465421e-9f + fl * 0.693147182f;
    /* exp(rh + rl) = 1 + rh + (rl + rh*rl + rh*rh*P(rh)) */
    z = rh * rh;
    p = _m_poly(rh, pow_ep, 6);
    tail = z * p + rh * rl;
    s1 = _m_2sum(1.0f, rh, &e1);
    r.f = s1 + ((e1 + rl) + tail);
    r.f = _m_pack(0, M_MANT(r) | 0x800000UL, M_EXP(r) + n);
sign:
    if (neg)
        r.u |= 0x80000000UL;
    return r.f;
}
