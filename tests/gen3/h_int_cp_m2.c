#include <stdio.h>
struct S { signed char c; int i; long l; unsigned char u; };
struct S gs[4]; unsigned char buf[16] = {0,1,2,3,4,5,6,7,0,9,10,11,12,13,14,15}; int ib[8] = {0,1,2,3,0,5,6,7};
int gi = 3;
int f(int p0, unsigned char * p1) { if (p1[7] > 3) return (int)((long)p0 * 70001L + gi); return (int)((long)p0 / 7 - gi); }
int main(void) { gs[1].u = 3; gs[2].u = 0; printf("%d %d %d\n", f((int)(300), buf+1), f((int)(300), buf+8), f((int)(5), buf+3)); return 0; }
