/* lc_strtoimax.c - strtoimax */
#include <stdlib.h>
#include <inttypes.h>

intmax_t strtoimax(const char *nptr, char **endptr, int base)
{
    return strtoll(nptr, endptr, base);
}
