#include <stdio.h>
#include <stdarg.h>
typedef long long LL; typedef unsigned long long ULL;
void p(const char *s, ULL v){ printf("%s %04x%04x%04x%04x\n", s, (unsigned)((v >> 48) & 0xffff), (unsigned)((v >> 32) & 0xffff), (unsigned)((v >> 16) & 0xffff), (unsigned)(v & 0xffff)); }
#ifdef HOST
typedef short I16; typedef unsigned short U16; typedef int L32; typedef unsigned U32;
#else
typedef int I16; typedef unsigned U16; typedef long L32; typedef unsigned long U32;
#endif
volatile I16 vi = -3; volatile U16 vu = 65533u; volatile L32 vl = -100000L; volatile U32 vul = 4000000000u;
volatile LL vll = -5000000000LL; volatile ULL vull = 0xfffffffffffffff0ULL; volatile signed char vc = -5; volatile unsigned char vuc = 250;
volatile int sh = 33;
LL addv(LL a, LL b){ return a + b; }
LL (*fp)(LL, LL) = addv;
LL vsum(int n, ...){ va_list ap; LL s = 0; va_start(ap, n); while (n--) s += va_arg(ap, LL); va_end(ap); return s; }
int sgn(LL x){ return x < 0 ? -1 : x > 0 ? 1 : 0; }
int usg(ULL x){ return x > 0x7fffffffffffffffULL; }
int main(void){
  LL t[4] = {10, 20, 30, 40};
  p("a", fp(vi, vll)); p("b", fp(vu, vull)); p("c", fp(vl, vul)); p("d", fp(vc, vuc));
  printf("%d %d %d %d\n", sgn(vi), sgn(vul), sgn(vll), sgn(0));
  printf("%d %d %d\n", usg(vi), usg(vul), usg(vull));
  printf("%d %d %d %d\n", vll < vi, vull > vul, (ULL)vi > vul, vll < vull);
  printf("%d %d %d %d\n", vi < vll, vul == 4000000000LL, vull == (ULL)-16, (LL)vull == -16);
  p("g", t[vi + 4]); p("h", *(t + (vll >> 32 & 3))); p("i", *(&t[3] - (vll & 1)));
  p("j", vll << 3); p("k", vull >> 4); p("l", vll >> sh); p("m", 1LL << sh); p("n", vull >> sh); p("o", (ULL)vi << sh);
  p("q", vll / 7); p("r", vll % 7); p("s", vull / vi); p("t", vull % 1000); p("u", vll / vl); p("v", vll * vl);
  p("w", (LL)vl * vl); p("x", (ULL)vul * vul); p("y", vll + vi); p("z", vull - vc);
  p("A", vll & vi); p("B", vll | vuc); p("C", vll ^ vul); p("D", ~vull);
  p("H", (LL)vc); p("I", (LL)vuc); p("J", (ULL)vi); p("K", (ULL)vu); p("L", (ULL)vl); p("M", (ULL)vul);
  p("N", (unsigned char)vll); p("O", (short)vull); p("P", (L32)vll); p("Q", (U32)vull);
  p("R", vi ? vll : vull); p("S", vc < 0 ? (ULL)vc : 0);
  { LL x = vll; x += vi; x -= vc; x *= 3; x ^= vul; p("T", x); x >>= 1; p("U", x); x = -x; p("V", x); }
  { ULL y = vull; y = y * 3 + vi; p("W", y); y = (y >> 60) | (y << 4); p("X", y); }
  p("Y", sizeof(LL)); p("Z", sizeof t);
  return 0;
}
