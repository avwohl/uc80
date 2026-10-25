/* lc_llrint.c - llrint */
#include "m.h"

/* ALIAS: llrint = llrintf llrintl */
long long llrint(double x)
{
    return _m_toll(_m_round(x, 4));
}
