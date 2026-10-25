/* lc_strtoull.c - strtoull */
#include <stdlib.h>

extern void _strtoint(const char *nptr, char **endptr, int base,
                      unsigned char *r, int nb, int is_signed);

unsigned long long strtoull(const char *nptr, char **endptr, int base)
{
    unsigned long long v;
    _strtoint(nptr, endptr, base, (unsigned char *)&v, 8, 0);
    return v;
}
