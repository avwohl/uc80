#include <stdio.h>
typedef long long LL; typedef unsigned long long ULL;
void p(const char *s, ULL v){ printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff), (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff), (unsigned)(v & 0xffff)); }
LL id(LL a){ return a; }
LL two(LL a, LL b){ return a * 1000 + b; }
LL mix(char c, LL a, int i, LL b, long l){ return a + b + c + i + l; }
LL ptr(LL *q, LL v){ *q = v; return *q + 1; }
LL sum(int n, ...);
#define K 40000
volatile int vi = 3;
int main(void){
  LL r; ULL u;
  p("a", id(K)); p("b", id(K + 1)); p("d", id(-K)); p("e", id(K * 2L)); p("f", id((LL)K << 20));
  p("g", id(0xffffffffU)); p("h", id(4294967295U)); p("i", id(2147483648u)); p("j", id(-2147483648LL));
  p("k", id('A')); p("l", id(-1)); p("m", id(65535)); p("n", id(65536)); p("o", id(1.0e10 > 0 ? 70000 : 0));
  p("q", two(K, K)); p("r", mix(-1, K, 7000, 0x100000000LL, -100000L));
  p("s", ptr(&r, 0x8000)); p("t", r);
  p("u", id(vi ? 0x7fffffffffffLL : 0)); p("v", id((LL)vi * 0x100000000LL));
  p("w", id(sizeof(LL) + K)); p("x", id(~0ULL));
  u = K; p("A", u); u = -K; p("B", u); r = 65535; p("C", r); r = 32768; p("D", r); r = -32769; p("E", r);
  r = 0x80000000; p("F", r); r = 0xffffffff; p("G", r); r = 2147483648; p("H", r);
  r = K; r *= K; p("I", r);
  return 0;
}
