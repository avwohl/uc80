/* (x op c1) op c2 is not folded to x op c3 when the constants have different
   widths: the inner operation wraps at the width of its own type.  This
   program is for a 16-bit int, so it has no host output; fold16.out is
   written by hand. */
#include <stdio.h>
typedef unsigned long long ULL;
static void p(const char *s, ULL v)
{
    printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff),
           (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff),
           (unsigned)(v & 0xffff));
}
volatile signed char x = 4;
volatile unsigned u = 65535u;
int main(void)
{
    p("a", (x + 65535u) - 256ULL);
    p("b", (u + 1u) + 100000UL);
    p("c", (u * 2u) * 3ULL);
    p("d", (u - 1u) - 70000UL);
    p("e", (u + 1u) + 5u);
    return 0;
}
