/* lc_scan_float.c - the floating-point conversion of the scanf engine.
 *
 * The characters of the number go into a buffer and strtod converts the
 * buffer.  The buffer takes about 118 characters of the mantissa.  Zeros
 * before the first nonzero digit are not stored, and digits past the end of
 * the buffer are counted: the exponent is corrected and one extra digit "1"
 * is added when a dropped digit is not zero, so strtod rounds the full
 * number correctly.  Like glibc, the reader takes "1e" and "1e+" as the
 * number 1 and leaves no character back.
 *
 * Returns 1 for a number, 0 for a matching failure, -1 at the end of the
 * input.  dst is the float to store into; it is NULL for %*f.
 *
 * The program refers to _sc_float_on (see scan_hook.py in uc80) to link this
 * module instead of lc_scan_fdefault, which defines _sc_float too.
 */
#include <stdlib.h>
#include "scan.h"

#define BUFSZ 127

static int sf_lower(int c)
{
    return (c >= 'A' && c <= 'Z') ? c + ('a' - 'A') : c;
}

/* ALIAS: _sc_float = _sc_float_on */
int _sc_float(struct sc *in, int width, void *dst)
{
    char buf[BUFSZ + 1];
    int n = 0, c, w, hex = 0, digits = 0, dot = 0, i;
    int nz = 0, sdot = 0, dropint = 0, zfrac = 0, sticky = 0, mn, ev = 0, esg = 1;
    char *end;
    double d;

    w = width ? width : 32767;
    /* sign */
    if (w <= 0)
        return 0;
    c = _sc_getc(in);
    w--;
    if (c < 0)
        return -1;
    if (c == '-' || c == '+') {
        buf[n++] = (char)c;
        if (w <= 0)
            return 0;
        c = _sc_getc(in);
        w--;
        if (c < 0)
            return -1;
    }
    if (sf_lower(c) == 'i' || sf_lower(c) == 'n') {
        /* inf, infinity, nan, nan(chars) */
        const char *word = (sf_lower(c) == 'i') ? "inf" : "nan";

        for (i = 0;;) {
            buf[n++] = (char)c;
            i++;
            if (word[i] == 0)
                break;
            c = (w > 0) ? _sc_getc(in) : -2;
            w--;
            if (sf_lower(c) != word[i]) {
                _sc_ungetc(in, c);
                return 0;
            }
        }
        if (word[0] == 'i') {
            /* optional "inity": a part of it is taken and ignored */
            static const char rest[] = "inity";

            for (i = 0; rest[i]; i++) {
                c = (w > 0) ? _sc_getc(in) : -2;
                w--;
                if (sf_lower(c) != rest[i]) {
                    _sc_ungetc(in, c);
                    break;
                }
                buf[n++] = (char)c;
            }
        } else {
            c = (w > 0) ? _sc_getc(in) : -2;
            w--;
            if (c == '(') {
                /* nan(n-char-sequence) */
                buf[n++] = '(';
                for (;;) {
                    c = (w > 0) ? _sc_getc(in) : -2;
                    w--;
                    if ((c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') ||
                        (c >= 'A' && c <= 'Z') || c == '_') {
                        if (n < BUFSZ - 2)
                            buf[n++] = (char)c;
                        continue;
                    }
                    if (c == ')')
                        buf[n++] = (char)c;
                    else
                        _sc_ungetc(in, c);
                    break;
                }
            } else {
                _sc_ungetc(in, c);
            }
        }
        goto convert;
    }
    if (c == '0') {
        buf[n++] = '0';
        digits = 1;
        c = (w > 0) ? _sc_getc(in) : -2;
        w--;
        if (c == 'x' || c == 'X') {
            buf[n++] = 'x';
            hex = 1;
            digits = 0;
            c = (w > 0) ? _sc_getc(in) : -2;
            w--;
        }
    }
    /* mantissa */
    for (;;) {
        if (c == '.' && !dot) {
            dot = 1;
            if (n < BUFSZ - 9) {
                buf[n++] = '.';
                sdot = 1;
            }
        } else if ((c >= '0' && c <= '9') ||
                   (hex && sf_lower(c) >= 'a' && sf_lower(c) <= 'f')) {
            digits = 1;
            if (!nz && c == '0') {
                if (dot)
                    zfrac++;
            } else {
                nz = 1;
                if (n < BUFSZ - 9) {
                    buf[n++] = (char)c;
                } else {
                    if (!dot)
                        dropint++;
                    if (c != '0')
                        sticky = 1;
                }
            }
        } else {
            break;
        }
        c = (w > 0) ? _sc_getc(in) : -2;
        w--;
    }
    mn = n;
    if (digits && (hex ? sf_lower(c) == 'p' : sf_lower(c) == 'e')) {
        int rew = dropint || zfrac || sticky;

        if (!rew)
            buf[n++] = (char)c;
        c = (w > 0) ? _sc_getc(in) : -2;
        w--;
        if (c == '-' || c == '+') {
            if (!rew)
                buf[n++] = (char)c;
            if (c == '-')
                esg = -1;
            c = (w > 0) ? _sc_getc(in) : -2;
            w--;
        }
        while (c >= '0' && c <= '9') {
            if (!rew && n < BUFSZ)
                buf[n++] = (char)c;
            if (ev < 99999)
                ev = ev * 10 + (c - '0');
            c = (w > 0) ? _sc_getc(in) : -2;
            w--;
        }
    }
    if (dropint || zfrac || sticky) {
        /* rebuild the number: mantissa, "1" for the dropped digits, and an
         * exponent with the corrections */
        long e = (long)esg * ev;
        char tmp[8];
        int t = 0;

        n = mn;
        if (sticky) {
            buf[n++] = '1';
            if (!sdot)
                dropint--;
        }
        e += (long)(hex ? 4 : 1) * (dropint - zfrac);
        buf[n++] = hex ? 'p' : 'e';
        if (e < 0) {
            buf[n++] = '-';
            e = -e;
        }
        do {
            tmp[t++] = (char)('0' + (int)(e % 10));
            e /= 10;
        } while (e);
        while (t)
            buf[n++] = tmp[--t];
    }
    _sc_ungetc(in, c);
    if (!digits)
        return 0;
convert:
    buf[n] = 0;
    d = strtod(buf, &end);
    /* Characters that are not in the number go back to the input.  A
     * string takes any number of them; a FILE keeps only the last one. */
    i = (int)(end - buf);
    while (n > i)
        _sc_ungetc(in, (unsigned char)buf[--n]);
    if (i == 0)
        return 0;
    if (dst)
        *(float *)dst = (float)d;
    return 1;
}
