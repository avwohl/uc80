/* lc_sscanf.c - sscanf */
#include <stdio.h>
#include <stdarg.h>

extern int _scan_str(const char *s, const char *fmt, char *ap);

int sscanf(const char *str, const char *fmt, ...)
{
    va_list ap;

    va_start(ap, fmt);
    return _scan_str(str, fmt, ap);
}
