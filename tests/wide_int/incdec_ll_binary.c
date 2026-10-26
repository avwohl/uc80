#include <stdio.h>
typedef long long LL; typedef unsigned long long ULL;
void p(const char *s, ULL v){ printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff), (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff), (unsigned)(v & 0xffff)); }
volatile LL v=0x100000003LL;
LL g(void){return 100;}
struct S{LL x; LL z;};
int main(void){
  LL x=v, s, a=4,b=4,r; LL *q=&x; struct S st={5,6};
  s = x++ * 2; p("a", s); p("ax", x);
  r = a++ + ++b; p("b", r);
  r = a++ + g(); p("c", r);
  a=4;b=4; r = a-- + --b; p("d", r);
  a=4;b=4; r = a++ ^ ++b; p("e", r);
  a=4;b=4; r = a++ < ++b; p("f", r);
  x=0x1ff; r = x++ & 0xff; p("g", r);
  x=9; r = (*q)++ / 3; p("h", r);
  r = st.x++ + ++st.z; p("i", r);
  r = ++a + b--; p("j", r);
  r = -(a++) + b; p("k", r);
  return 0;
}
