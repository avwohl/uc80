#include <stdio.h>
#ifdef HOST
typedef short I16; typedef unsigned short U16; typedef int L32; typedef unsigned U32;
#else
typedef int I16; typedef unsigned int U16; typedef long L32; typedef unsigned long U32;
#endif
typedef unsigned long long ULL;
static U16 f(U32 a){ return a; }
static I16 g(L32 a){ return a; }
static U16 h(ULL a, U32 b){ return a + b; }
volatile U32 vu = 0x12345678UL; volatile L32 vl = 70000L; volatile ULL vll = 0x1ffffffffULL;
int main(void){
  U32 r1 = f(vu); long r2 = g(vl); U32 r3 = h(vll, vu); ULL r4 = h(vll, 5);
  U32 r5 = f(0x12345678UL); long r6 = g(70000L);
  printf("%lx %ld %lx %lx %lx %ld\n", (long)r1, (long)r2, (long)r3, (long)(r4 & 0xffffffff), (long)r5, (long)r6);
  return 0;
}
