/* lc_strtod.c - strtod
 *
 * double, float and long double are all IEEE single precision on this
 * target, so one converter serves strtod, strtof and strtold.
 *
 * The converter does not use floating-point arithmetic.  It collects up to
 * 19 decimal digits (or 16 hex digits) in a 64-bit integer, multiplies by a
 * power of ten with 64-bit mantissa arithmetic, and rounds the 64-bit result
 * to a 24-bit mantissa (round to nearest, ties to even).  The error of the
 * power of ten is below 2^-57, so the result is correctly rounded except
 * for a decimal string that is within 2^-33 of an exact tie.
 */
#include <stdlib.h>
#include <errno.h>

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

/* Shift m left until the top bit is set; m is not zero. */
static void sd_norm(struct sd_big *a)
{
    int i;

    while (!(a->m[3] & 0x8000u)) {
        for (i = 3; i > 0; i--)
            a->m[i] = (a->m[i] << 1) | (a->m[i - 1] >> 15);
        a->m[0] <<= 1;
        a->e--;
    }
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

/* m = m * mul + add, the product must stay below 2^64 */
static void sd_muladd(unsigned *m, unsigned mul, unsigned add)
{
    unsigned long t;
    unsigned carry = add;
    int i;

    for (i = 0; i < 4; i++) {
        t = (unsigned long)m[i] * mul + carry;
        m[i] = (unsigned)t;
        carry = (unsigned)(t >> 16);
    }
}

/* Round the normalized number a to IEEE single bits (sign not included). */
static unsigned long sd_round(struct sd_big *a, int *inexact)
{
    int be, s, sh;
    unsigned long hi, lo, mant, bits, mask;
    int rb, sticky;

    /* value in [2^(e-1), 2^e): the biased exponent is e - 1 + 127 */
    be = a->e + 126;
    if (be >= 255)
        return 0x7f800000UL;
    s = 0;
    if (be < 1) {
        s = 1 - be;
        be = 1;
    }
    if (s > 24) {
        *inexact = 1;
        return 0;
    }
    hi = ((unsigned long)a->m[3] << 16) | a->m[2];
    lo = ((unsigned long)a->m[1] << 16) | a->m[0];
    sh = 8 + s;
    mant = (sh < 32) ? (hi >> sh) : 0;
    rb = (int)((hi >> (sh - 1)) & 1);
    mask = (1UL << (sh - 1)) - 1;
    sticky = ((hi & mask) != 0) || (lo != 0);
    *inexact = rb || sticky;
    if (rb && (sticky || (mant & 1)))
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
    int sticky = 0, exp = 0, eneg, edig, hex = 0, inexact = 0, n;
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

    for (n = 0; n < 4; n++)
        v.m[n] = 0;
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
        if (ndig < (hex ? 16 : 19)) {
            sd_muladd(v.m, hex ? 16 : 10, n);
            ndig++;
            if (dot)
                frac++;
        } else {
            if (n)
                sticky = 1;
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
    if (sticky)
        v.m[0] |= 1;
    v.e = 64;
    sd_norm(&v);
    if (hex) {
        /* value = M * 16^(dropped - frac) * 2^exp */
        v.e += exp + 4 * (dropped - frac);
    } else {
        n = exp + dropped - frac;       /* decimal exponent of M */
        if (n > 40) {
            r.u = 0x7f800000UL;
            errno = ERANGE;
            goto done;
        }
        if (n < -70) {
            r.u = 0;
            errno = ERANGE;
            goto done;
        }
        if (n) {
            sd_pow10(&pw, n);
            sd_mulhi(&v, &pw);
        }
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
