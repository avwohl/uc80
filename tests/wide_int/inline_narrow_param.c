#include <stdio.h>
typedef long long LL; typedef unsigned long long ULL;
typedef short I16; typedef unsigned short U16; typedef long L32;
void p(const char *s, ULL v){ printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff), (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff), (unsigned)(v & 0xffff)); }
LL f(I16 c, LL d){ return c + d; }
LL r3(I16 x){ return x; }
ULL r4(U16 x){ return x; }
ULL mix(L32 a, ULL b, I16 c, ULL d){ return a + b + c + d; }
LL r5(signed char x){ return x; }
LL r6(unsigned char x, LL y){ return x * y; }
long r7(I16 x){ return x; }
volatile LL v64 = 0x1fffffff0LL; volatile unsigned long u32 = 0xfffffff0UL;
volatile int vc = 40000;
int main(void){
  p("a", f(40000,0));
  p("b", r3(0xfffffff0UL));
  p("c", r3(v64));
  p("d", r3(u32));
  p("e", r4(0x12345678L));
  p("f", mix(1,2,40000,4));
  p("g", mix(1,2,vc,4));
  p("h", r5(300));
  p("i", r6(300, 2));
  p("j", r7(70000L));
  p("k", f(vc, 1));
  return 0;
}
