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
#include "scan.h"

#define EOF_ (-1)

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
    int c, w, neg = 0, any = 0, d, i, xc;
    unsigned t;

    for (i = 0; i < nb; i++)
        r[i] = 0;
    w = width ? width : 32767;
    c = sc_next(in, &w);
    if (c < 0)
        return -1;
    if (c == '-' || c == '+') {
        neg = (c == '-');
        c = sc_next(in, &w);
    }
    if ((base == 0 || base == 16) && c == '0') {
        any = 1;
        c = sc_next(in, &w);
        if (c == 'x' || c == 'X') {
            xc = c;
            c = sc_next(in, &w);
            if (c >= 0 && sc_digit(c) < 16) {
                base = 16;
                any = 0;
            } else {
                /* "0x" with no hex digit: the number is 0 and the x stays */
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
        t = d;
        for (i = 0; i < nb; i++) {
            t += (unsigned)r[i] * base;
            r[i] = (unsigned char)t;
            t >>= 8;
        }
        c = sc_next(in, &w);
    }
    _sc_ungetc(in, c);
    if (!any)
        return 0;
    if (neg) {
        t = 1;
        for (i = 0; i < nb; i++) {
            t += (unsigned char)~r[i];
            r[i] = (unsigned char)t;
            t >>= 8;
        }
    }
    return 1;
}

/* Parse the scan set after the '['.  Returns the position after the ']'. */
static const char *sc_set(const char *f, unsigned char *set)
{
    int neg = 0, i, c, last, hi;

    for (i = 0; i < 32; i++)
        set[i] = 0;
    if (*f == '^') {
        neg = 1;
        f++;
    }
    c = (unsigned char)*f;
    for (;;) {
        if (c == 0)
            break;
        f++;
        if (*f == '-' && f[1] != ']' && f[1] != 0) {
            last = (unsigned char)f[1];
            f += 2;
            for (hi = c; hi <= last; hi++)
                set[hi >> 3] |= (unsigned char)(1 << (hi & 7));
        } else {
            set[c >> 3] |= (unsigned char)(1 << (c & 7));
        }
        c = (unsigned char)*f;
        if (c == ']')
            break;
    }
    if (*f == ']')
        f++;
    if (neg)
        for (i = 0; i < 32; i++)
            set[i] = (unsigned char)~set[i];
    return f;
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

int _scan_run(struct sc *in, const char *fmt, char *ap)
{
    int done = 0, fc, c, width, mod, skip, nb, r, base, w, ok, conv;
    unsigned char *dst;
    unsigned char set[32];
    unsigned char num[8];
    const char *f = fmt;

    in->n = 0;
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
            if (fc == 'h') {
                mod = (mod == 'h') ? 'H' : 'h';
            } else if (fc == 'l') {
                mod = (mod == 'l') ? 'q' : 'l';
            } else if (fc == 'j' || fc == 'z' || fc == 't' || fc == 'L' ||
                       fc == 'q') {
                mod = fc;
            } else {
                break;
            }
            f++;
        }
        conv = (unsigned char)*f;
        if (conv == 0)
            goto done;
        f++;
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
            if (!skip) {
                dst = *(unsigned char **)ap;
                ap += sizeof(void *);
                nb = (mod == 'H') ? 1 : (mod == 'h') ? sizeof(short) :
                     (mod == 'l') ? sizeof(long) :
                     (mod == 'q' || mod == 'j' || mod == 'L') ?
                         sizeof(long long) :
                     (mod == 'z') ? sizeof(size_t) :
                     (mod == 't') ? sizeof(ptrdiff_t) : sizeof(int);
                sc_store(dst, nb, in->n);
            }
            continue;
        }
        if (conv == 'c') {
            w = width ? width : 1;
            if (!skip) {
                dst = *(unsigned char **)ap;
                ap += sizeof(void *);
            }
            c = _sc_getc(in);
            if (c < 0)
                goto input_fail;
            for (;;) {
                if (!skip)
                    *dst++ = (unsigned char)c;
                if (--w <= 0)
                    break;
                c = _sc_getc(in);
                if (c < 0)
                    break;
            }
            if (!skip)
                done++;
            continue;
        }
        if (conv == '[') {
            f = sc_set(f, set);
            if (!skip) {
                dst = *(unsigned char **)ap;
                ap += sizeof(void *);
            }
            w = width ? width : 32767;
            ok = 0;
            for (;;) {
                if (w <= 0)
                    break;
                c = _sc_getc(in);
                if (c < 0)
                    break;
                if (!(set[c >> 3] & (1 << (c & 7)))) {
                    _sc_ungetc(in, c);
                    break;
                }
                w--;
                ok++;
                if (!skip)
                    *dst++ = (unsigned char)c;
            }
            if (!ok) {
                if (c < 0)
                    goto input_fail;
                goto done;
            }
            if (!skip) {
                *dst = 0;
                done++;
            }
            continue;
        }
        /* all other conversions skip white space first */
        if (sc_skip(in) < 0)
            goto input_fail;
        if (conv == 's') {
            if (!skip) {
                dst = *(unsigned char **)ap;
                ap += sizeof(void *);
            }
            w = width ? width : 32767;
            while (w > 0) {
                c = _sc_getc(in);
                if (c < 0)
                    break;
                if (sc_space(c)) {
                    _sc_ungetc(in, c);
                    break;
                }
                w--;
                if (!skip)
                    *dst++ = (unsigned char)c;
            }
            if (!skip) {
                *dst = 0;
                done++;
            }
            continue;
        }
        if (conv == 'd' || conv == 'i' || conv == 'u' || conv == 'o' ||
            conv == 'x' || conv == 'X' || conv == 'p') {
            base = (conv == 'd' || conv == 'u') ? 10 :
                   (conv == 'i') ? 0 : (conv == 'o') ? 8 : 16;
            nb = (conv == 'p') ? sizeof(void *) :
                 (mod == 'H') ? 1 : (mod == 'h') ? sizeof(short) :
                 (mod == 'l') ? sizeof(long) :
                 (mod == 'q' || mod == 'j' || mod == 'L') ?
                     sizeof(long long) :
                 (mod == 'z') ? sizeof(size_t) :
                 (mod == 't') ? sizeof(ptrdiff_t) : sizeof(int);
            r = sc_int(in, base, width, num, nb);
            if (r < 0)
                goto input_fail;
            if (r == 0)
                goto done;
            if (!skip) {
                dst = *(unsigned char **)ap;
                ap += sizeof(void *);
                for (w = 0; w < nb; w++)
                    dst[w] = num[w];
                done++;
            }
            continue;
        }
        if (conv == 'f' || conv == 'e' || conv == 'g' || conv == 'a' ||
            conv == 'F' || conv == 'E' || conv == 'G' || conv == 'A') {
            if (!skip) {
                dst = *(unsigned char **)ap;
                ap += sizeof(void *);
            }
            r = _sc_float(in, width, skip ? (void *)0 : (void *)dst);
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
