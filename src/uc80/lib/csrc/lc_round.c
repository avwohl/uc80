/* lc_round.c - round: half away from zero */
#include "m.h"

/* ALIAS: round = roundf roundl */
double round(double x)
{
    return _m_round(x, 3);
}
