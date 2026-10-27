#include <stdio.h>
typedef long long LL;
#ifdef HOST
typedef short I16; typedef int L32;
#else
typedef int I16; typedef long L32;
#endif
static _Bool s1(I16 x){ return x; }
static _Bool s2(L32 x){ return x; }
static _Bool s3(LL x){ return x; }
static _Bool s4(const char *p){ return p; }
static _Bool s5(double d){ return d; }
static int t1(_Bool b){ return b; }
static int t2(_Bool a, _Bool b){ return a * 2 + b; }
static int t3(I16 k, _Bool a, _Bool b, _Bool c){ return k + a * 4 + b * 2 + c; }
int main(void){
  I16 x = 0x100; L32 y = 0x10000L; LL z = 0x100000000LL; double d = 0.1; char buf[2];
  _Bool b1 = x, b2 = y, b3 = z, b4 = d, b5 = buf;
  printf("%d%d%d%d%d\n", b1, b2, b3, b4, b5);
  printf("%d%d%d%d%d\n", s1(x), s2(y), s3(z), s4((const char *)0x100), s5(d));
  printf("%d %d %d\n", t1(x), t1(y), t1(z));
  printf("%d %d %d\n", t2(x, y), t2(z, 0), t2(0x200, 0x400));
  printf("%d\n", t3(1, 0x100, 0x10000L, 0x100000000LL));
  printf("%d%d%d\n", (_Bool)x, (_Bool)y, (_Bool)z);
  printf("%d%d%d\n", s1(0x100), s2(0x10000L), s3(0x100000000LL));
  printf("%d%d\n", t1(0x100), t1(0x100000000LL));
  return 0;
}
