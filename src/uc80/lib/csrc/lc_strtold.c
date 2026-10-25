/* lc_strtold.c - strtold (long double and double are the same type here) */
#include <stdlib.h>

long double strtold(const char *nptr, char **endptr)
{
    return (long double)strtod(nptr, endptr);
}
