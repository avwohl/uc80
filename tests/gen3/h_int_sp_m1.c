#include <stdio.h>
struct S { signed char c; int i; long l; unsigned char u; };
struct S gs[4]; unsigned char buf[16] = {0,1,2,3,4,5,6,7,0,9,10,11,12,13,14,15}; int ib[8] = {0,1,2,3,0,5,6,7};
int gi = 3;
int f(int p0, struct S * p1) { if ((unsigned char)(gi ^ p1->u)) return (int)((long)(unsigned long)gi * -4095L - (long)p0 * (unsigned)p0); return (signed char)p0 | -4464; }
int main(void) { gs[1].u = 3; gs[2].u = 0; printf("%d %d %d\n", f((int)(300), &gs[1]), f((int)(300), &gs[2]), f((int)(5), &gs[1])); return 0; }
