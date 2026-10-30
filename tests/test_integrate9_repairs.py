"""Repairs found by the verifiers of the integrate9 merge.

Each test runs a program and compares the output with the value that a
host C compiler gives.  The topics, one test each:

  - an enumeration bit-field is signed when an enumerator is negative;
  - a bit-field store whose address expression calls a 32-bit runtime
    helper (--int 32 used __tmp32 for both);
  - a local struct of 1 or 2 bytes initialized from an lvalue;
  - a block-scope typedef after a restore of the type scope;
  - ``array + n`` in a static initializer;
  - constexpr objects and offsetof in array bounds and enumerators;
  - enumerators and constant expressions in designators and widths;
  - bit-field initializers in a 4-byte unit (--int 32);
  - _Generic with integer constants (--int 32);
  - ``x % 1`` is a 0 of the type of x, not an int 0;
  - sizeof(__func__);
  - a static initializer that reads a variable is an error.
"""

import subprocess
import sys

import pytest

from tests.test_struct_return_abi import (  # noqa: F401
    REPO, build_and_run, pytestmark)

SRC = {}
WANT = {}
SRC['bf32'] = r'''#include <stdio.h>
struct S { unsigned f:7; unsigned g:9; } a[6];
struct Y { unsigned x:4; unsigned y:6; };
struct Y yy[3];
struct Y *sel(int k) { return &yy[k % 3]; }
int main(void){
  int i = 0, n = 3, k = 1;
  a[i++].f = 105;       printf("A %d %d\n", a[0].f, i);
  a[n/3].f = 4;         printf("B %d\n", a[1].f);
  a[n%4].f = 11;        printf("C %d\n", a[3].f);
  a[i%3].f += 1;        printf("D %d\n", a[1].f);
  a[(i+0)%3].f ^= 3;    printf("E %d\n", a[1].f);
  a[2].g = 300;
  a[(i+1)%3].f ^= a[2].g; printf("F %d\n", a[2].f);
  sel(k+1)->y += 7;     printf("G %d\n", yy[2].y);
  sel(k-1)->x = 9;      printf("H %d %d\n", yy[0].x, yy[0].y);
  return 0;}
'''
WANT['bf32'] = {16: 'A 105 1\nB 4\nC 11\nD 5\nE 6\nF 44\nG 7\nH 9 0\n', 32: 'A 105 1\nB 4\nC 11\nD 5\nE 6\nF 44\nG 7\nH 9 0\n'}
SRC['sinit'] = r'''#include <stdio.h>
struct N { unsigned char x, y; };
struct N n[3] = {{1,2},{3,4},{5,6}}; struct N *p = n+1; int k = 1;
struct S1 { unsigned x:3; unsigned y:13; } a[3];
int main(void){
  struct N b = n[1];
  struct N c = n[k];
  struct N d = *p;
  struct N e = p[0];
  struct N f = n[2], g = n[0];
  a[2].x = 1; a[2].y = 1002;
  struct S1 l = a[2];
  printf("%d %d | %d %d | %d %d | %d %d | %d %d | %d %d | %d %d\n",b.x,b.y,c.x,c.y,d.x,d.y,e.x,e.y,f.x,f.y,g.x,g.y,l.x,l.y);
  return 0;}
'''
WANT['sinit'] = {16: '3 4 | 3 4 | 3 4 | 3 4 | 5 6 | 1 2 | 1 1002\n', 32: '3 4 | 3 4 | 3 4 | 3 4 | 5 6 | 1 2 | 1 1002\n'}
SRC['btd'] = r'''#include <stdio.h>
int sum(int n, ...){ return n; }
int other(int a){ return a + 1; }
int main(void){
  typedef struct { int v; } W;
  W w = { 4 };
  struct { W in; int z; } nest = { { 5 }, 6 };
  printf("%d %d %d %d %d\n", sum(3,1,2,3), w.v, nest.in.v, nest.z, other(1));
  return 0;}
'''
WANT['btd'] = {16: '3 4 5 6 2\n', 32: '3 4 5 6 2\n'}
SRC['saddr'] = r'''#include <stdio.h>
static const int cv[] = {1,2,3};
static int nv[] = {1,2,3};
static long lv[] = {10L,20L,30L};
struct P { char c; long l; };
static struct P pv[] = {{1,2},{3,4},{5,6}};
int m2[2][3] = {{1,2,3},{4,5,6}};
const int *p1 = cv+1;
int *p2 = nv+1;
long *p3 = lv+2;
struct P *p4 = pv+1;
int *p5 = 2+nv;
int *p6 = nv+2-1;
int (*p7)[3] = m2+1;
struct S { const int *p; int *q; } s1 = { cv+1, nv+2 };
int main(void){
  printf("%d %d %ld %ld %d %d %d | %d %d\n",*p1,*p2,*p3,p4->l,*p5,*p6,(*p7)[1],*s1.p,*s1.q);
  return 0;}
'''
WANT['saddr'] = {16: '2 2 30 4 3 2 5 | 2 3\n', 32: '2 2 30 4 3 2 5 | 2 3\n'}
SRC['cexp'] = r'''#include <stdio.h>
#include <stddef.h>
constexpr int K = 5;
int a[K]; int b[K]; int z = 77;
enum { Z0 = K + 1, Y0 = K * 2 };
int arr2[Z0];
struct P { char a; long b; short c[3]; };
int arr3[offsetof(struct P, c) + 1];
int main(void){
  constexpr int e = 3;
  enum { Z = e + 1 };
  int loc[e] = { 1, 2, 3 };
  int loc2[Z];
  static int sl[K];
  int i;
  for (i = 0; i < K; i++) { a[i] = i; b[i] = 100 + i; sl[i] = i + 1; }
  printf("%d %d %d %d %d\n", a[4], b[0], z, (int)(sizeof a / sizeof a[0]), sl[4]);
  printf("%d %d %d %d\n", Z, loc[2], (int)(sizeof loc2 / sizeof loc2[0]), (int)(sizeof arr2 / sizeof arr2[0]));
  printf("%d\n", (int)(sizeof arr3 / sizeof arr3[0]) > 1);
  return 0;}
'''
WANT['cexp'] = {16: '4 100 77 5 5\n4 3 4 6\n1\n', 32: '4 100 77 5 5\n4 3 4 6\n1\n'}
SRC['bwid'] = r'''#include <stdio.h>
enum { K = 2, W = 3 };
int ga[] = { [W] = 9, [W-2] = 4 };
int main(void){
  {
    enum { K = 3, W = 5 };
    struct B { unsigned f:W; unsigned g:K; } b = { 31, 3 };
    printf("%d %d\n", b.f, b.g);
    int sa[] = { [K] = 9, [K-2] = 4 };
    printf("%d %d %d %d\n", sa[0], sa[1], sa[2], (int)(sizeof sa / sizeof sa[0]));
  }
  int lb[6] = { [W+1] = 5 };
  static int sb[] = { [2+W] = 7 };
  char s[] = { ['a'-'a'+2] = 'x' };
  printf("%d %d %d | %d %d | %d %d | %d %c\n", (int)(sizeof ga / sizeof ga[0]), ga[3], ga[1], lb[4], lb[0], (int)(sizeof sb / sizeof sb[0]), sb[5], (int)sizeof s, s[2]);
  return 0;}
'''
WANT['bwid'] = {16: '31 3\n0 4 0 4\n4 9 4 | 5 0 | 6 7 | 3 x\n', 32: '31 3\n0 4 0 4\n4 9 4 | 5 0 | 6 7 | 3 x\n'}
SRC['bfinit'] = r'''#include <stdio.h>
struct B { unsigned f:5; unsigned g:3; int h:9; };
struct L { unsigned long a:20; unsigned long b:12; };
int gx = 31; long gl = 0x12345; short sh = -3;
int main(void){
  int x = gx; long l = gl;
  struct B b0 = { 31, 3 };
  struct B b = { x, x & 3, sh };
  struct B c = { l, (int)(l >> 4), -(int)l };
  struct L d = { l, gl >> 12 };
  struct L e = { x, 4000 + x };
  printf("%d %d | %d %d %d | %d %d %d | %lu %lu | %lu %lu\n",b0.f,b0.g,b.f,b.g,b.h,c.f,c.g,c.h,(unsigned long)d.a,(unsigned long)d.b,(unsigned long)e.a,(unsigned long)e.b);
  return 0;}
'''
WANT['bfinit'] = {16: '31 3 | 31 3 -3 | 5 4 187 | 74565 18 | 31 4031\n', 32: '31 3 | 31 3 -3 | 5 4 187 | 74565 18 | 31 4031\n'}
SRC['gen32'] = r'''#include <stdio.h>
#define T(x) _Generic((x), int:"i", unsigned:"u", long:"l", unsigned long:"ul", default:"?")
int main(void){
  struct { int v; } w = { 4 };
  printf("%s %s %s %s %s %s\n", T(3), T(3u), T(3L), T(0x7fffffff), T(0xffffffff), T(w.v));
  return 0;}
'''
WANT['gen32'] = {16: 'i u l l ul i\n', 32: 'i u l i u i\n'}
SRC['casts32'] = r'''#include <stdio.h>
typedef long long LL; typedef unsigned long long ULL;
enum E { EA = 5, EB = -7 };
struct F { int a:5; unsigned b:7; enum E e:4; int m; } sf = { -3, 100, EA, 42 };
int arr[3] = { 7, -8, 9 }; int gi = 6; enum E ge = EB; short gs = -2;
int fn(void) { return -11; }
LL c1(void) { return (LL)sf.a + (LL)sf.b + (LL)sf.e; }
int main(void){
  int i = 1;
  printf("%lld %lld %lld %lld\n", (LL)ge, (LL)EB, (LL)sf.m, (LL)fn());
  LL r1 = (LL)(i++); LL r2 = (LL)(i += 2); LL r3 = (LL)(i, gi); LL r4 = (LL)(i ? gi : -1);
  printf("%lld %lld %lld %lld\n", r1, r2, r3, r4);
  printf("%lld %lld %lld %lld\n", (LL)!gi, (LL)(gi && i), (LL)-gi, (LL)~gi);
  printf("%lld %lld %lld %lld\n", (LL)*arr, (LL)arr[1], (LL)(gi << 3), (LL)(gi % 4));
  printf("%lld %lld %lld\n", (LL)(gi / 4), c1(), (LL)(ULL)(gs < gi));
  printf("%llu %llu\n", (ULL)gs, (ULL)(unsigned)gs);
  return 0;}
'''
WANT['casts32'] = {16: '-7 -7 42 -11\n1 4 6 6\n0 1 -6 -7\n7 -8 48 2\n1 102 1\n18446744073709551614 65534\n', 32: '-7 -7 42 -11\n1 4 6 6\n0 1 -6 -7\n7 -8 48 2\n1 102 1\n18446744073709551614 4294967294\n'}
SRC['modone'] = r'''#include <stdio.h>
struct S { unsigned b:16; unsigned short pad; } s = {5,0}, *p = &s;
struct T { signed b:16; } t = {-6}, *q = &t;
long g = 7; short sh = -6; unsigned x = 5;
int main(void){
  x /= ((1 ? sh : (g % 1)) | 1);
  printf("%u\n", x);
  s.b = 5; p->b /= ((1 ? q->b : (g % 1)) | 1);
  printf("%u\n", (unsigned)s.b);
  printf("%ld\n", (long)(1 ? sh : (g % 1)));
  return 0;}
'''
WANT['modone'] = {16: '65535\n65535\n-6\n'}


def _check(tmp_path, name, bits):
    flags = ("--int", "32") if bits == 32 else ()
    assert build_and_run(tmp_path, SRC[name], flags=flags) == WANT[name][bits]


@pytest.mark.parametrize("bits", [16, 32])
@pytest.mark.parametrize("name", ["bf32", "sinit", "btd", "saddr", "cexp",
                                  "bwid", "bfinit", "gen32", "casts32"])
def test_repair_program(tmp_path, name, bits):
    _check(tmp_path, name, bits)


def test_x_mod_1_keeps_the_type_of_x(tmp_path):
    _check(tmp_path, "modone", 16)


@pytest.mark.parametrize("flags", [(), ("--no-addr-cse", "--no-dead-store",
                                        "--no-blockops", "--no-chain-store"),
                                   ("-O0",)])
def test_block_typedef_with_passes_off(tmp_path, flags):
    assert build_and_run(tmp_path, SRC["btd"], flags=flags) == WANT["btd"][16]


ENUM_BF = r'''#include <stdio.h>
enum Neg { N_A = -3, N_B = 0, N_C = 5 };
enum Pos { P_A = 0, P_B = 1, P_C = 3 };
struct R { enum Neg n:4; enum Pos p:2; enum { A1 = -2, A2 = 1 } an:3; } r;
int main(void){
  r.n = N_A; r.p = P_C; r.an = A1;
  printf("%d %d %d\n", (int)r.n, (int)r.p, (int)r.an);
  switch (r.n) { case N_A: printf("neg\n"); break; default: printf("other\n"); }
  return 0;}
'''


@pytest.mark.parametrize("flags", [(), ("--int", "32")])
def test_enum_bitfield_signed_only_with_negative_enumerator(tmp_path, flags):
    assert build_and_run(tmp_path, ENUM_BF, flags=flags) == "-3 3 -2\nneg\n"


FUNC = r'''#include <stdio.h>
void hello(void){ printf("%d %s\n",(int)sizeof(__func__),__func__); }
int main(void){ hello(); printf("%d\n",(int)sizeof(__func__)); return 0;}
'''


def test_sizeof_func(tmp_path):
    assert build_and_run(tmp_path, FUNC) == "6 hello\n5\n"


def _compile(tmp_path, source, flags=()):
    c_file = tmp_path / "t.c"
    c_file.write_text(source)
    return subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), *flags, "-o",
         str(tmp_path / "t.mac")], capture_output=True, text=True,
        cwd=str(REPO))


def test_static_initializer_reading_a_variable_is_an_error(tmp_path):
    r = _compile(tmp_path, "const int e = 3;\nint main(void){ static int q = e;"
                 " return q; }\n")
    assert r.returncode != 0
    assert "initializer element is not constant" in r.stderr
    assert "t.c:2" in r.stderr or ":2:" in r.stderr


def test_static_initializer_with_constants_still_compiles(tmp_path):
    src = ("#include <stddef.h>\nenum { N = 3 };\nconstexpr int K = 4;\n"
           "int a[2]; int *p = a + 1; int (*f)(void); char *s = \"x\";\n"
           "struct Q { int x; int y; };\n"
           "long n = N * K + sizeof(int) + offsetof(struct Q, y);\n"
           "int main(void){ static int *q = &a[1]; return 0; }\n")
    r = _compile(tmp_path, src)
    assert r.returncode == 0, r.stderr
