/* lc_strtoumax.c - strtoumax */
#include <stdlib.h>
#include <inttypes.h>

uintmax_t strtoumax(const char *nptr, char **endptr, int base)
{
    return strtoull(nptr, endptr, base);
}
