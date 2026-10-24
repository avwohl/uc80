/* sglue.c - see sglue.h.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 */
#include <stdio.h>
#include <stdlib.h>
#include "posix_shim.h"
#define SGLUE_C
#include "sglue.h"

int sg_getc(void)
{
    int c;

    for (;;) {
        c = getchar();
        if (c == '\r')
            continue;
        if (c == EOF || c == 255 || c == 26)
            return EOF;
        return c;
    }
}

char *sg_fgets(char *buf, int n)
{
    int c, i;

    fflush(stdout);
    i = 0;
    while (i < n - 1) {
        c = sg_getc();
        if (c == EOF) {
            if (i == 0)
                return 0;
            break;
        }
        buf[i++] = (char)c;
        if (c == '\n')
            break;
    }
    buf[i] = 0;
    return buf;
}

#ifdef SG_NOFILE
int sg_fprintf(void *f, const char *fmt, ...) PX_VA
{
    va_list ap;
    int n;

    va_start(ap, fmt);
    n = vprintf(fmt, ap);
    va_end(ap);
    return n;
}

int sg_fputs(const char *s)
{
    printf("%s", s);
    return 0;
}

void sg_perror(const char *s)
{
    printf("%s: error\n", s);
}
#endif
