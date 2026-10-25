/* lc_lrint.c - lrint */
#include "m.h"

/* ALIAS: lrint = lrintf lrintl */
long lrint(double x)
{
    return (long)_m_round(x, 4);
}
