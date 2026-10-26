#include <stdio.h>
typedef long long LL; typedef unsigned long long ULL;
void p(const char *s, ULL v){ printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff), (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff), (unsigned)(v & 0xffff)); }
volatile int vi = 3; volatile LL vl = 0x1122334455667788LL;
struct In { LL x; char y; };
struct S { char c; struct In in; LL a[2]; };
LL f(LL a){ return a + 1; }
int main(void){
  LL d[4] = {[2] = 0x7fffffffffffLL, [0] = -3};
  struct S s = {1, {-7, 2}, {vl, 0x100000000LL}};
  struct S t = {.in.x = 0x100000001LL, .a[1] = -1};
  struct S u = {.c = 2, .a = {5, 6}};
  LL e[3] = {f(1), vl, vi};
  static LL st[2] = {0x100000000LL, -2};
  struct In arr[2] = {{1, 2}, {0x300000000LL, 4}};
  LL z[3] = {0};
  LL *pp = (LL[]){vl, 5};
  p("d0", d[0]); p("d1", d[1]); p("d2", d[2]);
  p("s", s.in.x); p("s", s.a[0]); p("s", s.a[1]);
  p("t", t.in.x); p("t", t.a[0]); p("t", t.a[1]);
  p("u", u.in.x); p("u", u.a[0]); p("u", u.a[1]);
  p("e", e[0]); p("e", e[1]); p("e", e[2]);
  p("st", st[0]); p("st", st[1]);
  p("arr", arr[0].x); p("arr", arr[1].x);
  p("z", z[2]); p("pp", pp[0]); p("pp", pp[1]);
  return 0;
}
