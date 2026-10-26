#include <stdio.h>
typedef long long LL;
volatile int vi = 0x100; volatile long vl = 0x10000L; volatile LL vll = 0x100000000LL; volatile float vf = 0.5f;
struct S { _Bool a; int k; _Bool b; };
_Bool g1 = 0x100, g2 = 0x10000L, g3 = 0x100000000LL, g4 = 0.5, g5 = 256;
_Bool ga[4] = {0x100, 0x200, 3, 0};
struct S gs = {0x100, 7, 0x10000};
struct S gs2 = {.b = 0x100, .a = 2};
const _Bool gc = 0x100;
enum { E = 0x100 };
_Bool ge = E;
_Bool gd = 1.0e-3;
_Bool gdz = 0.0;
_Bool gp = (_Bool)(char *)0x100;
char buf[2];
_Bool gq = 1;
_Bool gq2 = &buf[0] != 0;
int main(void){
  static _Bool s1 = 0x100, sa[2] = {0x200, 0x300};
  static struct S ss = {0x100, 1, 0x100};
  printf("%d%d%d%d%d\n", g1,g2,g3,g4,g5);
  printf("%d%d%d%d\n", ga[0],ga[1],ga[2],ga[3]);
  printf("%d %d %d\n", gs.a, gs.k, gs.b);
  printf("%d %d %d\n", gs2.a, gs2.k, gs2.b);
  printf("%d%d%d%d%d%d\n", gc, ge, gd, gdz, gq, gq2);
  printf("%d%d%d%d%d\n", s1, sa[0], sa[1], ss.a, ss.b);
  { _Bool a[3] = {0x100, vi, vl}; printf("%d%d%d\n", a[0], a[1], a[2]); }
  { _Bool a[3] = {vll, vf, 0}; printf("%d%d%d\n", a[0], a[1], a[2]); }
  { struct S s = {vi, 3, 0x100}; printf("%d%d%d\n", s.a, s.k, s.b); }
  { struct S s = {.b = vl, .a = vll}; printf("%d%d%d\n", s.a, s.k, s.b); }
  { struct S s = (struct S){vi, 3, 0x100}; printf("%d%d%d\n", s.a, s.k, s.b); }
  { struct S *p = &(struct S){0x100, 0x100, vl}; printf("%d%d%d\n", p->a, p->k != 0, p->b); }
  { _Bool a[2][2] = {{0x100, vi}, {vl, 0x200}}; printf("%d%d%d%d\n", a[0][0], a[0][1], a[1][0], a[1][1]); }
  { struct S s; s.a = 0x100; s.b = vl; _Bool *q = &s.a; *q = vi; printf("%d%d\n", s.a, *q); s.b = 0; s.b |= 0x100; printf("%d\n", s.b); }
  { _Bool x = 0; x += 0x100; printf("%d\n", x); x = 0; x |= vl; printf("%d\n", x); x = 0; x ^= vll; printf("%d\n", x); x = 1; x &= vi; printf("%d\n", x); x = 1; x *= 0x100; printf("%d\n", x);}
  { _Bool x = 0; x = vi; printf("%d\n", x++); printf("%d\n", x); x = 1; printf("%d\n", ++x); x = 0; x = x - 1; printf("%d\n", x); }
  { _Bool x; x = vf; printf("%d", x); x = -0.0f; printf("%d", x); x = (_Bool)-0.0; printf("%d", x); x = vi && 3; x = (_Bool)(vi * 2); printf("%d\n", x); }
  return 0;
}
