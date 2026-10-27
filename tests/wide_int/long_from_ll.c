#include <stdio.h>
#ifdef HOST
typedef int L32; typedef unsigned U32;
#else
typedef long L32; typedef unsigned long U32;
#endif
typedef long long LL;
volatile LL vl = 0x55C4297A771C5545LL;
L32 g1 = 0x55C4297A771C5545LL;
int main(void){
  L32 l = 0x55C4297A771C5545LL;
  L32 m = vl;
  L32 n; n = 0x55C4297A771C5545LL;
  L32 o; o = vl;
  int i = 0x55C4297A771C5545LL;
  int j = vl;
  printf("%lx %lx %lx %lx %lx %x %x\n", (unsigned long)(U32)l, (unsigned long)(U32)m, (unsigned long)(U32)n, (unsigned long)(U32)o, (unsigned long)(U32)g1, (unsigned)i & 0xffff, (unsigned)j & 0xffff);
  return 0;
}
