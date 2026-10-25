/* lc_scan_fdefault.c - the default floating-point conversion of the scanf
 * engine: every %f, %e, %g and %a is a matching failure.  A program that
 * needs them links lc_scan_float instead (see scan_hook.py in uc80).
 * CONCAT-SKIP: lc_scan_float defines _sc_float too. */
#include "scan.h"

int _sc_float(struct sc *in, int width, void *dst)
{
    return 0;
}
