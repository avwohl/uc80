#include <stdio.h>
typedef long long LL; typedef unsigned long long ULL;
void p(const char *s, ULL v){ printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff), (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff), (unsigned)(v & 0xffff)); }
volatile int vi = 2;
int main(void){
  ULL ub = 0x1234567; int i = 2; LL b;
  b = ub ^ (i & 1 ? 0x8000000000000000ULL : 0); p("a", b);
  i = 3;
  b = ub ^ (i & 1 ? 0x8000000000000000ULL : 0); p("b", b);
  b = ub ^ (vi & 1 ? 0x8000000000000000ULL : 0); p("c", b);
  b = ub ^ (vi & 2 ? 0x8000000000000000ULL : 0); p("d", b);
  ULL u = ub ^ (vi & 2 ? 0x8000000000000000ULL : 0); p("e", u);
  u = (vi & 2 ? 0x8000000000000000ULL : 0); p("f", u);
  u = (vi & 1 ? 0x8000000000000000ULL : 5); p("g", u);
  u = (vi & 1 ? 0x8000000000000000ULL : 0) + ub; p("h", u);
  u = ub + (vi & 2 ? 0x123456789ULL : 7); p("i", u);
  u = ub | (vi ? vi : 0x100000000ULL); p("j", u);
  b = vi ? ub : -1; p("k", b);
  b = vi ? -5 : ub; p("l", b);
  b = vi ? 5 : 7LL; p("m", b);
  b = ub - (vi > 5 ? 3 : 0x7fffffffffffLL); p("n", b);
  b = (vi ? 0 : 1) ? 1LL : ub; p("o", b);
  b = (vi == 2) ? (LL)vi << 40 : 0; p("p", b);
  b = (vi == 2) ? 0xffffffffULL : 0xffULL; p("q", b);
  b = (vi == 1) ? 0xffffffffULL : 0xffULL; p("r", b);
  return 0;
}
