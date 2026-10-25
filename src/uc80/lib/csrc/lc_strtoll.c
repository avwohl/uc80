/* lc_strtoll.c - strtoll */
#include <stdlib.h>

extern void _strtoint(const char *nptr, char **endptr, int base,
                      unsigned char *r, int nb, int is_signed);

long long strtoll(const char *nptr, char **endptr, int base)
{
    long long v;
    _strtoint(nptr, endptr, base, (unsigned char *)&v, 8, 1);
    return v;
}
