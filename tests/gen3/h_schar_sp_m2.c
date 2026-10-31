#include <stdio.h>
struct S { signed char c; int i; long l; unsigned char u; };
struct S gs[4]; unsigned char buf[16] = {0,1,2,3,4,5,6,7,0,9,10,11,12,13,14,15}; int ib[8] = {0,1,2,3,0,5,6,7};
int gi = 3;
int f(signed char p0, struct S * p1) { if (p1->u > 3) return (int)((long)p0 * 70001L + gi); return (int)((long)p0 / 7 - gi); }
int main(void) { gs[1].u = 3; gs[2].u = 0; printf("%d %d %d\n", f((signed char)(-5), &gs[1]), f((signed char)(-5), &gs[2]), f((signed char)(5), &gs[1])); return 0; }
