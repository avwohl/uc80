/* lc_remainder.c - remainder: x - n*y for n = x/y rounded to nearest even */
#include "m.h"

extern double remquo(double x, double y, int *quo);

/* ALIAS: remainder = remainderf remainderl */
double remainder(double x, double y)
{
    int q;

    return remquo(x, y, &q);
}
