/* lc_ceil.c - ceil */
#include "m.h"

/* ALIAS: ceil = ceilf ceill */
double ceil(double x)
{
    return _m_round(x, 2);
}
