#include <stdio.h>
#ifdef HOST
typedef short I16; typedef unsigned short U16; typedef int L32;
#else
typedef int I16; typedef unsigned int U16; typedef long L32;
#endif
typedef long long LL; typedef unsigned long long ULL;
I16 f2(I16 p0, I16 p1){ return p1; }
LL f3(I16 p0, I16 p1){ return p1; }
LL f4(LL p0, ULL p1){ return p1; }
LL f5(LL p0, L32 p1){ return p1; }
void p(const char *s, ULL v){ printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff), (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff), (unsigned)(v & 0xffff)); }
int main(void){
  printf("%d\n",(int)f2(1, (ULL)(char)(27187LL)));
  printf("%d\n",(int)f2(1, (char)(27187LL)));
  printf("%d\n",(int)f3(1, (ULL)(char)(27187LL)));
  printf("%d\n",(int)f3(1, (char)27187));
  printf("%d\n",(int)f4(1, (char)(27187LL)));
  printf("%d\n",(int)f5(1, (ULL)(char)(27187LL)));
  printf("%d\n",(int)f5(1, (ULL)(unsigned char)(27187LL)));
  p("a", f4(1, (ULL)(signed char)(200LL)));
  p("b", f4(1, (LL)(I16)(70000LL)));
  p("c", f4(1, (LL)(U16)(70000LL)));
  p("d", f4(1, (ULL)(L32)(0x1ffffffffLL)));
  p("e", f4(1, (ULL)(unsigned char)(-1LL)));
  p("f", f5(1, (LL)(_Bool)(256LL)));
  p("g", f5(1, (LL)(char)(0xffffLL)));
  return 0;
}
