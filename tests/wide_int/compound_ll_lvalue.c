#include <stdio.h>
typedef long long LL; typedef unsigned long long ULL;
void p(const char *s, ULL v){ printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff), (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff), (unsigned)(v & 0xffff)); }
#ifdef HOST
typedef short I16; typedef int L32;
#else
typedef int I16; typedef long L32;
#endif
LL a[4] = {1, 2, 3, 4};
volatile I16 vi = 1; volatile L32 vl = 100000L; volatile LL vll = 0x100000000LL;
int cnt;
int idx(void){ cnt++; return 1; }
LL *pp(void){ cnt++; return &a[2]; }
int main(void){
  I16 i = 0; LL *q = a; LL r;
  a[i++] += 5; p("a0", a[0]); p("i", i);
  a[idx()] += 7; p("a1", a[1]); p("cnt", cnt);
  *pp() += 0x100000000LL; p("a2", a[2]); p("cnt", cnt);
  r = (a[i++] *= 3); p("r", r); p("i", i); p("a1", a[1]);
  *q++ -= 1; p("a0", a[0]); p("q", q - a);
  r = (*q++ <<= 33); p("r", r); p("q", q - a);
  a[idx()] ^= vll; p("a1", a[1]);
  a[idx()] >>= 3; p("a1", a[1]);
  a[idx()] |= vl; p("a1", a[1]);
  a[idx()] &= ~0xffLL; p("a1", a[1]);
  r = a[idx()]++; p("r", r); p("a1", a[1]);
  r = --a[idx()]; p("r", r);
  a[idx()] %= 1000; p("a1", a[1]);
  a[idx()] /= 3; p("a1", a[1]);
  a[idx()] -= vll; p("a1", a[1]); p("cnt", cnt);
  { ULL u = 5; u -= 10; p("u", u); u <<= 63; p("u", u); u >>= 62; p("u", u);
    u = 0xffffffffffffffffULL; u += 1; p("u", u); u--; p("u", u); u *= u; p("u", u);
    LL s = -7; s /= 2; p("s", s); s %= 3; p("s", s); s = -1; s >>= 5; p("s", s); s = 0x7fffffffffffffffLL; s++; p("s", s); }
  return 0;
}
