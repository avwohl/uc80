/* lc_strtod.c - strtod
 *
 * double, float and long double are all IEEE single precision on this
 * target, so one converter serves strtod, strtof and strtold.
 *
 * The converter does not use floating-point arithmetic.  It collects up to
 * SD_K decimal (or hexadecimal) digits in a big integer.  The top 64 bits
 * of that integer, multiplied by a power of ten with 64-bit mantissa
 * arithmetic and rounded to a 24-bit mantissa (round to nearest, ties to
 * even), give the result.  The error of the 64-bit product is below 2^-54,
 * so only a decimal string within 2^-48 of an exact tie needs more work.
 * For such a string the converter compares the digits with the tie in big
 * integers, so every decimal string is correctly rounded.  A hexadecimal
 * string is exact in 64 bits and never needs the comparison.
 */
#include <stdlib.h>
#include <errno.h>

/* The longest tie of a float has 113 significant digits.  A string with
 * more than SD_K digits is cut after SD_K digits and a flag remembers a
 * nonzero digit in the rest.  The tie cannot match the cut digits, so the
 * flag decides only when the digits are the same as the tie. */
#define SD_K 116
#define SD_LIMBS 38

/* a big integer, little endian in 16-bit limbs, no zero limb at the top */
struct sd_num {
    unsigned n;
    unsigned d[SD_LIMBS];
};

static struct sd_num sd_a, sd_b;
static int sd_dec;      /* the digits in sd_a are decimal */
static int sd_q;        /* decimal exponent of the integer in sd_a */
static int sd_tail;     /* a nonzero digit after the first SD_K digits */
static int sd_lz;       /* sd_a was multiplied by 2^sd_lz (sd_top) */

/* value = (m / 2^64) * 2^e, m is little endian in 16-bit limbs */
struct sd_big {
    unsigned m[4];
    int e;
};

static void sd_mulhi(struct sd_big *a, const struct sd_big *b)
{
    unsigned p[8];
    unsigned long t;
    unsigned carry;
    int i, j;

    for (i = 0; i < 8; i++)
        p[i] = 0;
    for (i = 0; i < 4; i++) {
        carry = 0;
        for (j = 0; j < 4; j++) {
            t = (unsigned long)a->m[i] * b->m[j] + p[i + j] + carry;
            p[i + j] = (unsigned)t;
            carry = (unsigned)(t >> 16);
        }
        p[i + 4] = carry;
    }
    a->e += b->e;
    if (p[7] & 0x8000u) {
        for (i = 0; i < 4; i++)
            a->m[i] = p[i + 4];
    } else {
        for (i = 0; i < 4; i++)
            a->m[i] = (p[i + 4] << 1) | (p[i + 3] >> 15);
        a->e--;
    }
}

/* x = x * mul + add */
static void sd_bmul(struct sd_num *x, unsigned mul, unsigned add)
{
    unsigned long t;
    unsigned *p = x->d, *end = x->d + x->n;

    for (; p < end; p++) {
        t = (unsigned long)*p * mul + add;
        *p = (unsigned)t;
        add = (unsigned)(t >> 16);
    }
    if (add)
        x->d[x->n++] = add;
}

/* POS = POS * unit^k.  A negative K multiplies NEG instead, by unit^-k. */
static void sd_mulp(struct sd_num *pos, struct sd_num *neg, int k,
                    unsigned unit)
{
    unsigned big = (unit == 10) ? 10000u : 0x8000u;     /* unit^per */
    int per = (unit == 10) ? 4 : 15;

    if (k < 0) {
        k = -k;
        pos = neg;
    }
    for (; k >= per; k -= per)
        sd_bmul(pos, big, 0);
    for (; k > 0; k--)
        sd_bmul(pos, unit, 0);
}

/* v = the top 64 bits of sd_a, shifted left until the top bit is set (sd_a
 * is multiplied by 2^sd_lz).  A nonzero bit below them, or TAIL, sets the
 * lowest bit of v. */
static void sd_top(struct sd_big *v, int tail)
{
    unsigned top;
    int i, j = sd_a.n;

    for (top = sd_a.d[j - 1]; !(top & 0x8000u); top <<= 1)
        sd_lz++;
    if (sd_lz)
        sd_bmul(&sd_a, 1u << sd_lz, 0);
    for (i = 3; i >= 0; i--)
        v->m[i] = (--j >= 0) ? sd_a.d[j] : 0;
    while (j > 0)
        if (sd_a.d[--j])
            tail = 1;
    v->e = 16 * sd_a.n - sd_lz;
    if (tail)
        v->m[0] |= 1;
}

/* Compare the decimal number in sd_a (10^sd_q times the integer, and a
 * little more if sd_tail) with the tie (2 * MF + 1) * 2^E1.  Result: less
 * than 0, 0 or more than 0 for less, equal, greater. */
static int sd_cmp_mid(unsigned long mf, int e1)
{
    unsigned long t = 2 * mf + 1;
    unsigned i;
    int c;

    sd_b.d[0] = (unsigned)t;
    sd_b.d[1] = (unsigned)(t >> 16);
    sd_b.n = sd_b.d[1] ? 2 : 1;
    sd_mulp(&sd_a, &sd_b, sd_q, 10);
    sd_mulp(&sd_b, &sd_a, e1 + sd_lz, 2);   /* sd_a has the factor 2^sd_lz */
    c = (int)sd_a.n - (int)sd_b.n;
    for (i = sd_a.n; c == 0 && i-- > 0; )
        if (sd_a.d[i] != sd_b.d[i])
            c = (sd_a.d[i] > sd_b.d[i]) ? 1 : -1;
    return c ? c : sd_tail;
}

static void sd_pow10(struct sd_big *r, int k)
{
    struct sd_big b;
    int i;

    for (i = 0; i < 4; i++)
        r->m[i] = 0;
    r->m[3] = 0x8000u;
    r->e = 1;
    if (k < 0) {
        k = -k;
        b.m[3] = 0xCCCCu; b.m[2] = 0xCCCCu; b.m[1] = 0xCCCCu;
        b.m[0] = 0xCCCDu;
        b.e = -3;
    } else {
        b.m[3] = 0xA000u; b.m[2] = 0; b.m[1] = 0; b.m[0] = 0;
        b.e = 4;
    }
    while (k) {
        if (k & 1)
            sd_mulhi(r, &b);
        k >>= 1;
        if (k)
            sd_mulhi(&b, &b);
    }
}

static int sd_isdig(unsigned char c)
{
    return c >= '0' && c <= '9';
}

static int sd_hexval(unsigned char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

/* Case-insensitive prefix test: word is lower case. */
static int sd_match(const char *s, const char *word)
{
    int n = 0;
    unsigned char c;

    while (*word) {
        c = (unsigned char)s[n];
        if (c >= 'A' && c <= 'Z')
            c += 'a' - 'A';
        if (c != (unsigned char)*word)
            return 0;
        word++;
        n++;
    }
    return n;
}

/* Round the normalized number a to IEEE single bits (sign not included).
 * When a decimal number is within 2^-48 of a tie, the big integers decide. */
static unsigned long sd_round(struct sd_big *a, int *inexact)
{
    int be, s, up, sticky, rb, i;
    unsigned low, *m = a->m;
    unsigned long mant, bits;

    /* value in [2^(e-1), 2^e): the biased exponent is e - 1 + 127 */
    be = a->e + 126;
    if (be >= 255)
        return 0x7f800000UL;
    s = 0;
    if (be < 1) {
        s = 1 - be;
        be = 1;
    }
    *inexact = 1;
    if (s > 26)
        return 0;               /* below 2^-152: far from the smallest tie */
    /* A subnormal number: shift right to the unit 2^-149, keep a sticky bit */
    for (sticky = 0; s > 0; s--) {
        sticky |= m[0] & 1;
        for (i = 0; i < 3; i++)
            m[i] = (m[i] >> 1) | (m[i + 1] << 15);
        m[3] >>= 1;
    }
    m[0] |= sticky;
    /* The mantissa is bits 63 to 40, the rounding bit is bit 39 */
    mant = ((unsigned long)m[3] << 8) | (m[2] >> 8);
    rb = (m[2] >> 7) & 1;
    low = m[2] & 0x7F;               /* bits 38 to 32 */
    sticky = low || m[1] || m[0];
    *inexact = rb || sticky;
    up = rb && (sticky || (mant & 1));
    /* near: a decimal number is within 2^-48 of the tie, which is
     * (2 * mant + 1) * 2^(be - 151) */
    if (sd_dec && (rb ? (low == 0 && m[1] == 0 && m[0] < 0x4000u)
                      : (low == 0x7F && m[1] == 0xFFFFu &&
                         m[0] >= 0xC000u))) {
        sticky = sd_cmp_mid(mant, be - 151);
        *inexact = 1;
        up = sticky > 0 || (sticky == 0 && (mant & 1));
    }
    if (up)
        mant++;
    bits = ((unsigned long)(be - 1) << 23) + mant;
    if (bits >= 0x7f800000UL)
        return 0x7f800000UL;
    return bits;
}

double strtod(const char *nptr, char **endptr)
{
    const char *s = nptr, *p;
    unsigned char c;
    int neg = 0, any = 0, ndig = 0, dropped = 0, frac = 0, dot = 0;
    int tail = 0, exp = 0, eneg, edig, hex = 0, inexact = 0, n;
    struct sd_big v, pw;
    unsigned long bits;
    union {
        double d;
        unsigned long u;
    } r;

    while (*s == ' ' || (*s >= 9 && *s <= 13))
        s++;
    if (*s == '-' || *s == '+') {
        neg = (*s == '-');
        s++;
    }
    r.u = 0;

    n = sd_match(s, "inf");
    if (n) {
        s += n;
        n = sd_match(s, "inity");
        s += n;
        r.u = 0x7f800000UL;
        goto done;
    }
    n = sd_match(s, "nan");
    if (n) {
        s += n;
        if (*s == '(') {
            p = s + 1;
            while ((*p >= '0' && *p <= '9') || (*p >= 'a' && *p <= 'z') ||
                   (*p >= 'A' && *p <= 'Z') || *p == '_')
                p++;
            if (*p == ')')
                s = p + 1;
        }
        r.u = 0x7fc00000UL;
        goto done;
    }

    sd_a.n = 0;
    sd_dec = 0;
    sd_lz = 0;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X') &&
        (sd_hexval(s[2]) >= 0 ||
         (s[2] == '.' && sd_hexval(s[3]) >= 0))) {
        hex = 1;
        s += 2;
    }

    p = s;
    for (;; p++) {
        c = (unsigned char)*p;
        if (c == '.' && !dot) {
            dot = 1;
            continue;
        }
        n = hex ? sd_hexval(c) : (sd_isdig(c) ? c - '0' : -1);
        if (n < 0)
            break;
        any = 1;
        if (n == 0 && ndig == 0) {
            /* leading zero: nothing to store */
            if (dot)
                frac++;
            continue;
        }
        if (ndig < SD_K) {
            sd_bmul(&sd_a, hex ? 16 : 10, n);
            ndig++;
            if (dot)
                frac++;
        } else {
            if (n)
                tail = 1;
            if (!dot)
                dropped++;
        }
    }
    if (!any) {
        if (endptr)
            *endptr = (char *)nptr;
        return 0.0;
    }
    s = p;
    c = (unsigned char)*s;
    if (hex ? (c == 'p' || c == 'P') : (c == 'e' || c == 'E')) {
        p = s + 1;
        eneg = 0;
        if (*p == '-' || *p == '+') {
            eneg = (*p == '-');
            p++;
        }
        if (sd_isdig((unsigned char)*p)) {
            edig = 0;
            while (sd_isdig((unsigned char)*p)) {
                if (edig < 3000)
                    edig = edig * 10 + (*p - '0');
                p++;
            }
            exp = eneg ? -edig : edig;
            s = p;
        }
    }

    if (ndig == 0) {
        /* all digits were zero */
        r.u = 0;
        goto done;
    }
    sd_top(&v, tail);
    if (hex) {
        /* value = M * 16^(dropped - frac) * 2^exp */
        v.e += exp + 4 * (dropped - frac);
    } else {
        n = exp + dropped - frac;       /* decimal exponent of the integer */
        /* the value is in [10^(n + ndig - 1), 10^(n + ndig)) */
        if (n + ndig > 40) {
            r.u = 0x7f800000UL;
            errno = ERANGE;
            goto done;
        }
        if (n + ndig <= -46) {
            r.u = 0;
            errno = ERANGE;
            goto done;
        }
        if (n) {
            sd_pow10(&pw, n);
            sd_mulhi(&v, &pw);
        }
        sd_dec = 1;
        sd_q = n;
        sd_tail = tail;
    }
    bits = sd_round(&v, &inexact);
    if (bits == 0x7f800000UL || (bits < 0x00800000UL && inexact))
        errno = ERANGE;
    r.u = bits;
done:
    if (neg)
        r.u |= 0x80000000UL;
    if (endptr)
        *endptr = (char *)s;
    return r.d;
}
