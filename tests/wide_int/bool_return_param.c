#include <stdio.h>
typedef long long LL; typedef unsigned long long ULL;
#ifdef HOST
typedef short I16; typedef int L32; typedef unsigned short U16; typedef unsigned U32;
#else
typedef int I16; typedef long L32; typedef unsigned U16; typedef unsigned long U32;
#endif
volatile I16 vi = 0x100; volatile U16 vu = 0x8000; volatile L32 vl = 0x10000L; volatile U32 vul = 0x80000000UL; volatile LL vll = 0x100000000LL;
volatile ULL vull = 0x8000000000000000ULL;
volatile float vf = 0.5f, vnz = -0.0f; volatile double vd = 0.25, vdnz = -0.0;
volatile short vs = 0x100; volatile unsigned char vc = 0; 
char buf[300];
_Bool r_i(I16 x){ return x; }
_Bool r_u(U16 x){ return x; }
_Bool r_l(L32 x){ return x; }
_Bool r_ul(U32 x){ return x; }
_Bool r_ll(LL x){ return x; }
_Bool r_ull(ULL x){ return x; }
_Bool r_f(float x){ return x; }
_Bool r_d(double x){ return x; }
_Bool r_p(char *x){ return x; }
_Bool r_c(unsigned char x){ return x; }
int p_i(_Bool b){ return b; }
int p2_i(int k, _Bool b){ return b + k; }
int p3(_Bool a, _Bool b){ return a*2 + b; }
_Bool r_sum(I16 a, I16 b){ return a + b; }
_Bool r_and(I16 a, I16 b){ return a & b; }
_Bool r_shl(I16 a){ return a << 8; }
_Bool r_mul(I16 a, I16 b){ return a * b; }
_Bool r_lmul(L32 a, L32 b){ return a * b; }
_Bool r_ldiff(L32 a, L32 b){ return a - b; }
_Bool r_llsub(LL a, LL b){ return a - b; }
_Bool r_ternary(int k, I16 a){ return k ? a : 0; }
int main(void){
  char *p = (char *)(buf + 256);
  printf("%d%d%d%d%d%d%d%d%d%d\n", r_i(0x100), r_u(0x8000), r_l(0x10000L), r_ul(0x80000000UL), r_ll(0x100000000LL), r_ull(0x8000000000000000ULL), r_f(0.5f), r_d(0.25), r_p(p), r_c(0));
  printf("%d%d%d%d%d%d%d%d%d%d\n", r_i(vi), r_u(vu), r_l(vl), r_ul(vul), r_ll(vll), r_ull(vull), r_f(vf), r_d(vd), r_p((char*)(buf+256)), r_c(vc));
  printf("%d%d\n", r_f(vnz), r_d(vdnz));
  printf("%d%d%d%d%d%d%d%d\n", p_i(0x100), p_i(vi), p_i(vu), p_i(vl), p_i(vul), p_i(vll), p_i(vull), p_i(vf));
  printf("%d%d%d%d\n", p_i(vd), p_i(vnz), p_i(vdnz), p_i((int)(long)p));
  printf("%d%d%d\n", p2_i(1, 0x100), p2_i(2, vi), p2_i(3, vll));
  printf("%d%d%d\n", p3(vi, vi), p3(0x100, 0x200), p3(vll, 0));
  printf("%d%d%d%d%d%d%d%d\n", r_sum(0x80, 0x80), r_and(0x100, 0x100), r_shl(1), r_mul(16, 16), r_lmul(256, 256), r_ldiff(0x10000L, 0), r_llsub(0x100000000LL, 0), r_ternary(1, 0x100));
  printf("%d%d%d\n", r_sum(vi, 0), r_and(vi, vi), r_mul(vi, 1));
  { _Bool a = vi, b = vl, c = vll, d = vf, e = vull, f = vs; printf("%d%d%d%d%d%d\n", a,b,c,d,e,f);
    a = 0x100; b = 0x10000L; c = 0x100000000LL; d = 256.0; e = 0; f = vc; printf("%d%d%d%d%d%d\n", a,b,c,d,e,f);
    a = vs; a |= 0; b = vi; b &= 1; c = 0; c |= vll; printf("%d%d%d\n", a, b, c);
    c = 0; c += 0x100; printf("%d\n", c); c = 1; c += 255; c += 1; printf("%d\n", c);
    c = 0; c |= 0x100; printf("%d\n", c); }
  { _Bool a[3] = {0x100, vi, vll}; printf("%d%d%d\n", a[0], a[1], a[2]); }
  { struct { _Bool a, b; } s = {vi, 0x100}; printf("%d%d\n", s.a, s.b); }
  { int t = (_Bool)vi + (_Bool)0x100 + !!vi + (vi != 0); printf("%d\n", t); }
  { _Bool q = (vi ? 0x100 : 0); printf("%d\n", q); }
  return 0;
}
