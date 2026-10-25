/* lc_strtoul.c - strtoul */
#include <stdlib.h>

extern void _strtoint(const char *nptr, char **endptr, int base,
                      unsigned char *r, int nb, int is_signed);

unsigned long strtoul(const char *nptr, char **endptr, int base)
{
    unsigned long v;
    _strtoint(nptr, endptr, base, (unsigned char *)&v, 4, 0);
    return v;
}
