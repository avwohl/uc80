/* lc_scan_core.c - the scanf engine, used by scanf, fscanf, sscanf and the
 * v versions.
 *
 * The engine takes the characters from a struct sc (see scan.h).  Integers
 * go into a byte array of the size of the target, so %d, %ld, %lld and %hhd
 * share one reader and no 32-bit or 64-bit runtime routine is linked.
 *
 * Return value: the number of items assigned; EOF when the input ends
 * before the first item was assigned.  The standard says a conversion
 * "0x" with no digit after it is "0"; here it is a matching failure,
 * because a stream can push back only one character.
 */
#include <stddef.h>
#include <string.h>
#include "scan.h"

#define EOF_ (-1)

extern int _sc_muladd(unsigned char *r, int nb, int mul, int add);
extern void _sc_negate(unsigned char *r, int nb);

int _sc_getc(struct sc *in)
{
    int c = in->get(in);

    if (c >= 0)
        in->n++;
    return c;
}

void _sc_ungetc(struct sc *in, int c)
{
    if (c >= 0) {
        in->unget(in, c);
        in->n--;
    }
}

static int sc_space(int c)
{
    return c == ' ' || (c >= 9 && c <= 13);
}

/* The next character inside the width; -2 when the width is used up. */
static int sc_next(struct sc *in, int *w)
{
    if (*w <= 0)
        return -2;
    (*w)--;
    return _sc_getc(in);
}

static int sc_digit(int c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'z')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'Z')
        return c - 'A' + 10;
    return 99;
}

/* Skip white space.  Returns 0, or -1 at the end of the input. */
static int sc_skip(struct sc *in)
{
    int c;

    do {
        c = _sc_getc(in);
    } while (sc_space(c));
    if (c < 0)
        return -1;
    _sc_ungetc(in, c);
    return 0;
}

/* Read an integer of nb bytes into r.  base is 0 for %i.
 * Returns 1, 0 for a matching failure, -1 for the end of the input. */
static int sc_int(struct sc *in, int base, int width, unsigned char *r, int nb)
{
    int c, w, neg = 0, any = 0, d, xc, nb2;

    memset(r, 0, nb);
    w = width ? width : 32767;
    c = sc_next(in, &w);
    if (c < 0)
        return -1;
    if (c == '-' || c == '+') {
        neg = (c == '-');
        c = sc_next(in, &w);
    }
    if ((base == 0 || base == 16 || base == 2) && c == '0') {
        any = 1;
        c = sc_next(in, &w);
        if (c >= 0 && ((base != 2 && (c == 'x' || c == 'X')) ||
                       (base != 16 && (c == 'b' || c == 'B')))) {
            xc = c;
            nb2 = (c == 'x' || c == 'X') ? 16 : 2;
            c = sc_next(in, &w);
            if (c >= 0 && sc_digit(c) < nb2) {
                base = nb2;
                any = 0;
            } else {
                /* "0x" or "0b" with no digit: the number is 0 and the
                 * letter stays */
                _sc_ungetc(in, c);
                _sc_ungetc(in, xc);
                c = -2;
            }
        } else if (base == 0) {
            base = 8;
        }
    } else if (base == 0) {
        base = 10;
    }
    for (;;) {
        d = (c >= 0) ? sc_digit(c) : 99;
        if (d >= base)
            break;
        any = 1;
        _sc_muladd(r, nb, base, d);
        c = sc_next(in, &w);
    }
    _sc_ungetc(in, c);
    if (!any)
        return 0;
    if (neg)
        _sc_negate(r, nb);
    return 1;
}

/* Is c in the scan set whose text follows the '[' at f?  *endp gets the
 * position after the closing ']'.  A set can start with ^ and can have
 * ranges (a-z); a ']' that comes first is a member. */
static int sc_inset(int c, const char *f, const char **endp)
{
    int neg = 0, ok = 0, lo, first = 1;

    if (*f == '^') {
        neg = 1;
        f++;
    }
    for (;;) {
        lo = (unsigned char)*f;
        if (lo == 0 || (lo == ']' && !first))
            break;
        first = 0;
        f++;
        if (*f == '-' && f[1] != ']' && f[1] != 0) {
            if (c >= lo && c <= (unsigned char)f[1])
                ok = 1;
            f += 2;
        } else if (c == lo) {
            ok = 1;
        }
    }
    *endp = (*f == ']') ? f + 1 : f;
    return ok != neg;
}

/* Store a signed value of nb bytes. */
static void sc_store(void *dst, int nb, long v)
{
    unsigned char *p = (unsigned char *)dst;
    int i;

    for (i = 0; i < nb; i++) {
        if (i < 4)
            p[i] = (unsigned char)(v >> (8 * i));
        else
            p[i] = (v < 0) ? 0xff : 0;
    }
}

/* the size of the target for the length modifiers: none, hh, h, l, ll, j,
 * z, t, L */
static const unsigned char sc_size[] = {
    sizeof(int), 1, sizeof(short), sizeof(long), sizeof(long long),
    sizeof(long long), sizeof(size_t), sizeof(ptrdiff_t), sizeof(long long)
};

/* the integer conversions and the base of each (0: read the prefix) */
static const char sc_int_convs[] = "duoxXpib";
static const unsigned char sc_base[] = {10, 10, 8, 16, 16, 16, 0, 2};
static const char sc_float_convs[] = "feEgGaAF";

static char *sc_ap;                     /* the next argument */

static void *sc_arg(void)
{
    void *p = *(void **)sc_ap;

    sc_ap += sizeof(void *);
    return p;
}

int _scan_run(struct sc *in, const char *fmt, char *ap)
{
    int done = 0, fc, c, width, mod, skip, nb, r, w, conv, count;
    unsigned char *dst = 0;
    unsigned char num[8];
    const char *f = fmt, *set = 0, *endp, *p;

    in->n = 0;
    sc_ap = ap;
    while ((fc = (unsigned char)*f++) != 0) {
        if (sc_space(fc)) {
            sc_skip(in);
            continue;
        }
        if (fc != '%') {
            c = _sc_getc(in);
            if (c < 0)
                goto input_fail;
            if (c != fc) {
                _sc_ungetc(in, c);
                goto done;
            }
            continue;
        }
        /* conversion specification */
        skip = 0;
        width = 0;
        mod = 0;
        if (*f == '*') {
            skip = 1;
            f++;
        }
        while (*f >= '0' && *f <= '9') {
            width = width * 10 + (*f - '0');
            f++;
        }
        for (;;) {
            fc = *f;
            if (fc == 'h')
                mod = (mod == 2) ? 1 : 2;
            else if (fc == 'l')
                mod = (mod == 3) ? 4 : 3;
            else if (fc == 'j')
                mod = 5;
            else if (fc == 'z')
                mod = 6;
            else if (fc == 't')
                mod = 7;
            else if (fc == 'L' || fc == 'q')
                mod = 8;
            else
                break;
            f++;
        }
        conv = (unsigned char)*f;
        if (conv == 0)
            goto done;
        f++;
        nb = sc_size[mod];
        if (conv == '%') {
            if (sc_skip(in) < 0)
                goto input_fail;
            c = _sc_getc(in);
            if (c != '%') {
                _sc_ungetc(in, c);
                goto done;
            }
            continue;
        }
        if (conv == 'n') {
            if (!skip)
                sc_store(sc_arg(), nb, in->n);
            continue;
        }
        if (conv == 'c' || conv == 's' || conv == '[') {
            if (conv == '[') {
                set = f;
                sc_inset(-2, f, &f);
            } else if (conv == 's' && sc_skip(in) < 0) {
                goto input_fail;
            }
            if (!skip)
                dst = sc_arg();
            w = width ? width : (conv == 'c' ? 1 : 32767);
            count = 0;
            for (;;) {
                if (w <= 0)
                    break;
                c = _sc_getc(in);
                if (c < 0)
                    break;
                if (conv == 's' ? sc_space(c) :
                    (conv == '[' && !sc_inset(c, set, &endp))) {
                    _sc_ungetc(in, c);
                    break;
                }
                w--;
                count++;
                if (!skip)
                    *dst++ = (unsigned char)c;
            }
            if (!count) {
                if (c < 0)
                    goto input_fail;
                goto done;
            }
            if (!skip) {
                if (conv != 'c')
                    *dst = 0;
                done++;
            }
            continue;
        }
        /* all other conversions skip white space first */
        if (sc_skip(in) < 0)
            goto input_fail;
        p = strchr(sc_int_convs, conv);
        if (p) {
            if (conv == 'p')
                nb = sizeof(void *);
            r = sc_int(in, sc_base[p - sc_int_convs], width, num, nb);
            if (r < 0)
                goto input_fail;
            if (r == 0)
                goto done;
            if (!skip) {
                dst = sc_arg();
                memcpy(dst, num, nb);
                done++;
            }
            continue;
        }
        if (strchr(sc_float_convs, conv)) {
            dst = skip ? 0 : (unsigned char *)sc_arg();
            r = _sc_float(in, width, dst);
            if (r < 0)
                goto input_fail;
            if (r == 0)
                goto done;
            if (!skip)
                done++;
            continue;
        }
        /* unknown conversion: stop */
        goto done;
    }
done:
    return done;
input_fail:
    return done ? done : EOF_;
}
