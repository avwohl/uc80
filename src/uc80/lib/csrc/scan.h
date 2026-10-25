/* scan.h - the input source that the scanf engine reads from.
 *
 * A source has a get function and an unget function.  The engine counts the
 * characters it takes in "n" (this is the value for %n).  A source is a
 * string, a FILE, or the console.  The sscanf family does not link any
 * BDOS code, because only the console and FILE sources refer to the BDOS.
 */
#ifndef SCAN_H
#define SCAN_H

struct sc {
    int (*get)(struct sc *);            /* next character, or -1 */
    void (*unget)(struct sc *, int);    /* push back the last character */
    const unsigned char *s;             /* string source */
    void *fp;                           /* FILE source */
    int n;                              /* characters taken so far */
};

int _sc_getc(struct sc *in);
void _sc_ungetc(struct sc *in, int c);
int _scan_run(struct sc *in, const char *fmt, char *ap);
int _sc_float(struct sc *in, int width, void *dst);

#endif
