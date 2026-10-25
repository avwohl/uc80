/* lc_strtoint.c - shared core of strtol, strtoul, strtoll and strtoull.
 *
 * Reads an integer of NB bytes (4 or 8) into R, little endian.  The
 * digits go into the byte array one at a time, so no 32-bit or 64-bit
 * multiply routine from the runtime library is linked.
 *
 * Returns nothing.  *endptr is set as the C standard requires; errno is
 * set to ERANGE on overflow (the value is then the limit) and to EINVAL
 * for a base that is not 0 or 2 to 36.
 */
#include <errno.h>
#include <string.h>

extern int _sc_muladd(unsigned char *r, int nb, int mul, int add);
extern void _sc_negate(unsigned char *r, int nb);

static int digit_value(unsigned char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'z')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'Z')
        return c - 'A' + 10;
    return 99;
}

void _strtoint(const char *nptr, char **endptr, int base,
               unsigned char *r, int nb, int is_signed)
{
    const char *s = nptr;
    int neg = 0, ovf = 0, any = 0, i, d;

    memset(r, 0, nb);
    while (*s == ' ' || (*s >= 9 && *s <= 13))
        s++;
    if (*s == '-' || *s == '+') {
        neg = (*s == '-');
        s++;
    }
    if (base != 0 && (base < 2 || base > 36)) {
        errno = EINVAL;
        if (endptr)
            *endptr = (char *)nptr;
        return;
    }
    if ((base == 0 || base == 16) && s[0] == '0' &&
        (s[1] == 'x' || s[1] == 'X') && digit_value(s[2]) < 16) {
        s += 2;
        base = 16;
    } else if ((base == 0 || base == 2) && s[0] == '0' &&
               (s[1] == 'b' || s[1] == 'B') && digit_value(s[2]) < 2) {
        s += 2;
        base = 2;
    } else if (base == 0) {
        base = (s[0] == '0') ? 8 : 10;
    }
    for (;;) {
        d = digit_value(*s);
        if (d >= base)
            break;
        any = 1;
        s++;
        if (!ovf && _sc_muladd(r, nb, base, d))
            ovf = 1;
    }
    if (endptr)
        *endptr = (char *)(any ? s : nptr);
    if (!any)
        return;
    if (!ovf && is_signed && r[nb - 1] >= 0x80) {
        /* The magnitude may be 2^(8*nb-1) when the number is negative. */
        ovf = 1;
        if (neg && r[nb - 1] == 0x80) {
            ovf = 0;
            for (i = 0; i < nb - 1; i++)
                if (r[i])
                    ovf = 1;
        }
    }
    if (ovf) {
        errno = ERANGE;
        memset(r, (is_signed && neg) ? 0 : 0xff, nb);
        if (is_signed)
            r[nb - 1] = neg ? 0x80 : 0x7f;
        return;
    }
    if (neg)
        _sc_negate(r, nb);
}
