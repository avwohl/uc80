/* lc_rint.c - rint and nearbyint: half to even */
#include "m.h"

/* ALIAS: rint = rintf rintl nearbyint nearbyintf nearbyintl */
double rint(double x)
{
    return _m_round(x, 4);
}
