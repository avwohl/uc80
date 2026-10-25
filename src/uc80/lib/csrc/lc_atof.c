/* lc_atof.c - atof */
#include <stdlib.h>

double atof(const char *nptr)
{
    return strtod(nptr, (char **)0);
}
