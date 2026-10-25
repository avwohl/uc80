/* lc_trunc.c - trunc */
#include "m.h"

/* ALIAS: trunc = truncf truncl */
double trunc(double x)
{
    return _m_round(x, 0);
}
