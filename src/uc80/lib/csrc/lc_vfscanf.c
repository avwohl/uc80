/* lc_vfscanf.c - vfscanf */
#include <stdio.h>
#include <stdarg.h>

extern int _scan_file(FILE *fp, const char *fmt, char *ap);

int vfscanf(FILE *fp, const char *fmt, va_list ap)
{
    return _scan_file(fp, fmt, ap);
}
