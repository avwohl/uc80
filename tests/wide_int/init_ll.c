#include <stdio.h>
typedef long long LL; typedef unsigned long long ULL;
void p(const char *s, ULL v){ printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff), (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff), (unsigned)(v & 0xffff)); }
volatile int vi = 3;
LL gg[3] = {1, -2, 0x123456789abcdefLL};
struct S { char c; LL a; int b; ULL u; };
int main(void){
  ULL la[4] = {6857969344806328257ULL, 256ULL, 5ULL, 256ULL};
  LL lb[3] = {-1, 70000, vi};
  LL lc[6] = {vi, 1};
  struct S s = {1, -5, 2, 0xfedcba9876543210ULL};
  struct S t = {.u = 7, .a = -9};
  LL m[2][2] = {{1, 2}, {-3, 0x100000000LL}};
  p("a0", la[0]); p("a1", la[1]); p("a3", la[3]);
  p("b0", lb[0]); p("b1", lb[1]); p("b2", lb[2]);
  p("c0", lc[0]); p("c1", lc[1]); p("c5", lc[5]);
  p("s", s.a); p("su", s.u); p("t", t.a); p("tu", t.u);
  p("m", m[1][0]); p("m", m[1][1]); p("m", m[0][1]);
  p("g", gg[1]); p("g", gg[2]);
  return 0;
}
