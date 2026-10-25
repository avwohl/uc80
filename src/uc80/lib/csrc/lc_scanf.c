/* lc_scanf.c - scanf */
#include <stdio.h>
#include <stdarg.h>

extern int _scan_stdin(const char *fmt, char *ap);

int scanf(const char *fmt, ...)
{
    va_list ap;

    va_start(ap, fmt);
    return _scan_stdin(fmt, ap);
}
