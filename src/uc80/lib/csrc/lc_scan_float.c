/* lc_scan_float.c - the floating-point conversion of the scanf engine.
 *
 * The characters of the number go into a buffer and strtod converts the
 * buffer.  The buffer takes up to 126 characters; the rest of a longer
 * number is dropped.  Like glibc, the reader takes "1e" and "1e+" as the
 * number 1 and leaves no character back.
 *
 * Returns 1 for a number, 0 for a matching failure, -1 at the end of the
 * input.  dst is the float to store into; it is NULL for %*f.
 */
#include <stdlib.h>
#include "scan.h"

#define BUFSZ 127

static int sf_lower(int c)
{
    return (c >= 'A' && c <= 'Z') ? c + ('a' - 'A') : c;
}

int _sc_float(struct sc *in, int width, void *dst)
{
    char buf[BUFSZ + 1];
    int n = 0, c, w, hex = 0, digits = 0, dot = 0, i;
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
        } else if ((c >= '0' && c <= '9') ||
                   (hex && sf_lower(c) >= 'a' && sf_lower(c) <= 'f')) {
            digits = 1;
        } else {
            break;
        }
        if (n < BUFSZ - 8)
            buf[n++] = (char)c;
        c = (w > 0) ? _sc_getc(in) : -2;
        w--;
    }
    if (digits && (hex ? sf_lower(c) == 'p' : sf_lower(c) == 'e')) {
        buf[n++] = (char)c;
        c = (w > 0) ? _sc_getc(in) : -2;
        w--;
        if (c == '-' || c == '+') {
            buf[n++] = (char)c;
            c = (w > 0) ? _sc_getc(in) : -2;
            w--;
        }
        while (c >= '0' && c <= '9') {
            if (n < BUFSZ)
                buf[n++] = (char)c;
            c = (w > 0) ? _sc_getc(in) : -2;
            w--;
        }
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
