/* lc_scalbln.c - scalbln: x * 2^n with a long n */
#include "m.h"

extern double scalbn(double x, int n);

/* ALIAS: scalbln = scalblnf scalblnl */
double scalbln(double x, long n)
{
    if (n > 1000)
        n = 1000;
    if (n < -1000)
        n = -1000;
    return scalbn(x, (int)n);
}
