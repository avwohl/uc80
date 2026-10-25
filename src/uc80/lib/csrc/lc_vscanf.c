/* lc_vscanf.c - vscanf */
#include <stdio.h>
#include <stdarg.h>

extern int _scan_stdin(const char *fmt, char *ap);

int vscanf(const char *fmt, va_list ap)
{
    return _scan_stdin(fmt, ap);
}
