/* lc_vsscanf.c - vsscanf */
#include <stdio.h>
#include <stdarg.h>

extern int _scan_str(const char *s, const char *fmt, char *ap);

int vsscanf(const char *str, const char *fmt, va_list ap)
{
    return _scan_str(str, fmt, ap);
}
