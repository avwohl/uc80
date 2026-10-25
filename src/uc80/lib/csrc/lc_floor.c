/* lc_floor.c - floor */
#include "m.h"

/* ALIAS: floor = floorf floorl */
double floor(double x)
{
    return _m_round(x, 1);
}
