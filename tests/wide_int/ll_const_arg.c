/* A long long constant argument above 32767, and the exact repro of the
   xor with a conditional 64-bit constant. */
#include <stdio.h>
typedef long long LL; typedef unsigned long long ULL;
static void p(const char *s, ULL v)
{
    printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff),
           (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff),
           (unsigned)(v & 0xffff));
}
LL id(LL a) { return a; }
ULL uid(ULL a) { return a; }
LL sum3(LL a, LL b, LL c) { return a + b * 3 + c * 7; }
int main(void)
{
    ULL ub = 0x1234567;
    int i = 2;
    LL b = ub ^ (i & 1 ? 0x8000000000000000ULL : 0);
    p("repro", b);
    p("a", id(45826LL));
    p("b", id(32768));
    p("c", id(65535LL));
    p("d", id(-40000LL));
    p("e", id(0x123456789LL));
    p("f", uid(0xfedcba9876543210ULL));
    p("g", sum3(40000, 50000, 60000));
    p("h", sum3(-40000LL, 0x10000LL, 0x100000000LL));
    return 0;
}
