/* lc_scan_file.c - the FILE source of the scanf engine (fscanf, vfscanf).
 * A FILE takes one pushed-back character from ungetc.  The console is
 * different: BDOS has no pushback, so the console source in lc_scan_stdin
 * keeps its own pushed-back character. */
#include <stdio.h>
#include "scan.h"

extern int _scan_stdin(const char *fmt, char *ap);

static int sf_get(struct sc *in)
{
    return fgetc((FILE *)in->fp);
}

static void sf_unget(struct sc *in, int c)
{
    ungetc(c, (FILE *)in->fp);
}

int _scan_file(FILE *fp, const char *fmt, char *ap)
{
    struct sc in;

    if (fp == stdin)
        return _scan_stdin(fmt, ap);
    in.get = sf_get;
    in.unget = sf_unget;
    in.fp = fp;
    return _scan_run(&in, fmt, ap);
}
