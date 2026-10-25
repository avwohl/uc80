/* lc_fscanf.c - fscanf */
#include <stdio.h>
#include <stdarg.h>

extern int _scan_file(FILE *fp, const char *fmt, char *ap);

int fscanf(FILE *fp, const char *fmt, ...)
{
    va_list ap;

    va_start(ap, fmt);
    return _scan_file(fp, fmt, ap);
}
