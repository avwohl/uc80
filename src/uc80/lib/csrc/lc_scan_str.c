/* lc_scan_str.c - the string source of the scanf engine (sscanf, vsscanf).
 * This module has no BDOS call, so sscanf is in the ROM library. */
#include "scan.h"

static int ss_get(struct sc *in)
{
    if (*in->s == 0)
        return -1;
    return *in->s++;
}

static void ss_unget(struct sc *in, int c)
{
    in->s--;
}

int _scan_str(const char *str, const char *fmt, char *ap)
{
    struct sc in;

    in.get = ss_get;
    in.unget = ss_unget;
    in.s = (const unsigned char *)str;
    return _scan_run(&in, fmt, ap);
}
