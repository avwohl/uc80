#include <stdio.h>
typedef long long LL; typedef unsigned long long ULL;
void p(const char *s, ULL v){ printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff), (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff), (unsigned)(v & 0xffff)); }
struct S0 { char b; long m1; };
struct S0 gs = {7, -2L};
long gl = -2L; unsigned long gul = 0xfffffff0UL; int gi = -3; unsigned gu = 65533u; signed char gc = -4; unsigned char guc = 252;
ULL ret(char p25, ULL p26, LL p27){ return p26; }
LL retl(LL x, ULL y){ return x + y; }
ULL g8;
int main(void){
  g8 = ret(1, gs.m1, 0); p("a", g8);
  p("b", ret(1, gl, 0)); p("c", ret(1, gul, 0)); p("d", ret(1, gi, 0)); p("e", ret(1, gu, 0));
  p("f", ret(1, gc, 0)); p("g", ret(1, guc, 0)); p("h", ret(1, -2L, 0)); p("i", ret(1, 0xfffffff0UL, 0));
  p("j", retl(gl, gul)); p("k", retl(gi, gu)); p("l", retl(gc, guc));
  p("m", retl(-1, 65535u)); p("n", retl(gs.m1, gs.m1));
  return 0;
}
