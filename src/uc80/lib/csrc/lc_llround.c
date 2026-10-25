/* lc_llround.c - llround */
#include "m.h"

/* ALIAS: llround = llroundf llroundl */
long long llround(double x)
{
    return _m_toll(_m_round(x, 3));
}
