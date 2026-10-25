/* lc_atoll.c - atoll */
#include <stdlib.h>

long long atoll(const char *nptr)
{
    return strtoll(nptr, (char **)0, 10);
}
