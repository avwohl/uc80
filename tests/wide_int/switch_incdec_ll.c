#include <stdio.h>
#ifdef HOST
typedef int L32; typedef unsigned U32;
#else
typedef long L32; typedef unsigned long U32;
#endif
typedef long long LL; typedef unsigned long long ULL;
void p(const char *s, ULL v){ printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff), (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff), (unsigned)(v & 0xffff)); }
struct S0 { ULL m1; LL m2; ULL m3; };
volatile LL vz = 0; volatile L32 vlz = 0;
int sw(LL x){
  switch (x) {
  case -32768LL: return 1;
  case 32768LL: return 2;
  case 65536LL: return 3;
  case 0x100000000LL: return 4;
  case 37118LL: return 5;
  case 0: return 6;
  default: return 7;
  }
}
int swl(L32 x){
  switch (x) {
  case 65536L: return 3;
  case 0x10000001L: return 4;
  case -1L: return 5;
  default: return 7;
  }
}
int main(void){
  struct S0 ls = {6341526829131549145ULL, 281474976710656LL, 2147483647ULL};
  L32 g; ULL h; LL k;
  g = --ls.m3; printf("%lx\n", (unsigned long)(U32)g);
  p("m3", ls.m3);
  g = ls.m2++; printf("%lx\n", (unsigned long)(U32)g);
  p("m2", ls.m2);
  h = ls.m1--; p("h", h); p("m1", ls.m1);
  k = ++ls.m1; p("k", k);
  ULL a[2] = {0xffffffffffffULL, 5}; ULL *pa = a;
  h = ++*pa; p("h", h); h = (*pa)--; p("h", h); h = pa[1]++; p("h", h); p("a1", a[1]);
  h = ++a[1]; p("h", h);
  printf("%d %d %d %d %d %d %d\n", sw(vz), sw(65536LL), sw(0x100000000LL), sw(-32768LL), sw(5), sw(37118LL), sw(0x200000000LL));
  printf("%d %d %d %d\n", swl(vlz), swl(65536L), swl(0x10000001L), swl(-1L));
  return 0;
}
