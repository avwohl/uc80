/* lc_lround.c - lround */
#include "m.h"

/* ALIAS: lround = lroundf lroundl */
long lround(double x)
{
    return (long)_m_round(x, 3);
}
