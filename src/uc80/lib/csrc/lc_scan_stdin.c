/* lc_scan_stdin.c - the console source of the scanf engine.
 *
 * The console returns CP/M characters: Ctrl-Z (26) is the end of the
 * input, and the Enter key gives CR (13), which scanf takes as white space.
 * A character that the engine pushes back stays here until the next
 * scanf call, so "scanf("%d")" does not lose the character that ended the
 * number.
 */
#include <stdio.h>
#include "scan.h"

static int ssi_back;            /* the pushed-back character plus 1, or 0 */

static int ssi_get(struct sc *in)
{
    int c;

    if (ssi_back) {
        c = ssi_back - 1;
        ssi_back = 0;
        return c;
    }
    c = getchar();
    if (c == 26)
        return -1;
    return c;
}

static void ssi_unget(struct sc *in, int c)
{
    ssi_back = c + 1;
}

int _scan_stdin(const char *fmt, char *ap)
{
    struct sc in;

    in.get = ssi_get;
    in.unget = ssi_unget;
    return _scan_run(&in, fmt, ap);
}
