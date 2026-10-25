/* lc_strtol.c - strtol */
#include <stdlib.h>

extern void _strtoint(const char *nptr, char **endptr, int base,
                      unsigned char *r, int nb, int is_signed);

long strtol(const char *nptr, char **endptr, int base)
{
    long v;
    _strtoint(nptr, endptr, base, (unsigned char *)&v, 4, 1);
    return v;
}
