#include <stdio.h>
#ifdef HOST
typedef int L32; typedef unsigned U32;
#else
typedef long L32; typedef unsigned long U32;
#endif
typedef long long LL; typedef unsigned long long ULL;
void p(const char *s, ULL v){ printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff), (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff), (unsigned)(v & 0xffff)); }
#ifdef HOST
typedef short I16;
#else
typedef int I16;
#endif
I16 g8 = 4;
LL ga[2] = {-864717811597727140LL, -9223372036854775807LL-1};
ULL ua[4] = {2147483647ULL, 10286813485921217239ULL, 12ULL, 14886820912033583551ULL};
ULL *gp = &ua[3];
struct S0 { LL m1; };
struct S0 gs = {-9223372036854775807LL-1};
volatile int vi = 3;
int main(void){
  U32 l = 54666u;
  g8 = (~ga[0]); printf("%x\n", (unsigned)g8 & 0xffff);
  l = (~gs.m1); printf("%lx\n", (unsigned long)(U32)l);
  gp = &ua[((unsigned long)(-14LL)) & 3]; p("gp", *gp);
  gp = &ua[vi]; p("gp", *gp);
  return 0;
}
