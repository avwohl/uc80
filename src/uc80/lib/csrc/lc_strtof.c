/* lc_strtof.c - strtof (float and double are the same type here) */
#include <stdlib.h>

float strtof(const char *nptr, char **endptr)
{
    return (float)strtod(nptr, endptr);
}
