"""Bug round 6: a macro parameter inside a string literal, and 32-bit
sums of narrow operands under --int=32."""

import pytest

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401


def test_macro_parameter_is_not_replaced_inside_literals(tmp_path):
    src = r"""#include <stdio.h>
#define OUT(s) printf("%s %d\n", s, 1)
#define CH(s) printf("%c%c%s\n", 's', s, "s")
#define NAME(s) #s "-" "s"
#define PASTE(a, b) a ## b
#define VA(f, ...) printf(f "|__VA_ARGS__|" __VA_OPT__(,) __VA_ARGS__)
int main(void)
{
  int xy = 5;
  OUT("x");
  CH('q');
  puts(NAME(abc));
  printf("%d\n", PASTE(x, y));
  VA("%d", 7);
  puts("");
  return 0;
}
"""
    assert build_and_run(tmp_path, src) == \
        "x 1\nsqs\nabc-s\n5\n7|__VA_ARGS__|\n"


def test_macro_expansion_boundaries_run(tmp_path):
    """Tokens stay separate at the end of a macro call, an object-like
    macro that names itself expands once, and #__VA_ARGS__ keeps one
    space where the source had white space."""
    src = r"""#include <stdio.h>
#define T(t) t
#define ID(x) x
#define EMPTY
#define SV(...) #__VA_ARGS__
int var = 4, AA = 5;
#define var (var + 1)
#define AA BB + 1
#define BB AA * 2
#define VO(a, ...) a __VA_OPT__(+ 100) + 0
int main(void)
{
  T(int)x = 3;
  int a = 10, r;
  r = a ID(-)-1;
  printf("%d %d\n", x, r);
  printf("%d\n", var);
  printf("%d\n", AA);
  puts(SV(1, 2, 3));
  puts(SV(a ,  b,c));
  printf("%d %d %d\n", VO(1), VO(1, 2), VO(1, EMPTY));
  r = a -EMPTY-1;
  printf("%d\n", r);
  return 0;
}
"""
    assert build_and_run(tmp_path, src) == \
        "3 11\n5\n11\n1, 2, 3\na , b,c\n1 101 1\n11\n"


# The expected text is the output of the host cc (int has 32 bits there).
INT32_SRC = r"""#include <stdio.h>
volatile int vn = 0, vc = 1;
volatile unsigned short vp = 40000, vq = 3;
volatile short vs = -32768;
volatile signed char vm = -1;
volatile unsigned char va = 1, vb = 2;
volatile signed char vd = 3;
static int g(unsigned char sx, unsigned char sy, signed char sd, unsigned short sw)
{ return sx + sy + sd + sw; }
static int s6(unsigned char a, signed char b, unsigned short c, short d, unsigned char e, unsigned short f)
{ return a - b + c - d + e + f; }
static int m3(unsigned short a, signed char b, unsigned short c) { return a * b + c * 2 - (a & c) + (a ^ c) + (a | b); }
static int sh1(unsigned short p, int n) { return (p >> n) + 1; }
static int sh2(unsigned short p, int n) { return ~(p >> n); }
static int sh3(unsigned short p, int n) { return -(p >> n); }
static int sh4(unsigned short p, int n) { return (p >> n) * 2; }
static int sh5(unsigned short p, int n) { return (p >> n) - 100000; }
static int sh6(unsigned short p, unsigned short q) { return (p >> 0) + (q >> 0); }
static int pl(unsigned short p) { return (+p) + 1; }
static int dv1(unsigned short p, unsigned short q) { return (p / q) + 1; }
static int dv2(unsigned short p, unsigned short q) { return (p % q) + 1; }
static int dv3(short p, short q) { return (p / q) + 1; }
static int dv4(unsigned short p, unsigned short q) { return -(p / q); }
static int ng(short a, short b, short c) { return (-a) + (b + c); }
static int nt(short a, int n) { return -(a >> 1) & ((~a) - n); }
static int loc(unsigned short p, int n) { int x = p >> n; return x + 1; }
static int cmp(unsigned short p, int n) { return ((p >> n) < 0) + ((p >> n) > 32768) * 2; }
int main(void)
{
  printf("%d\n", g(1, 2, 3, 40000));
  printf("%d\n", g(va, vb, vd, vp));
  printf("%d\n", s6(255, -128, 65535, -32768, 255, 65535));
  printf("%d\n", s6(va, vm, vp, vs, vb, vp));
  printf("%d\n", m3(vp, vd, vq));
  printf("%d %d %d %d %d %d\n", sh1(vp, vn), sh2(vp, vn), sh3(vp, vn), sh4(vp, vn), sh5(vp, vn), sh6(vp, vp));
  printf("%d %d %d %d %d\n", pl(vp), dv1(vp, vq / 3), dv2(vp, vq * 20000), dv3(vs, -1), dv4(vp, 1));
  printf("%d %d %d %d %d\n", ng(-1, 26853, -1), nt(vs, 7), loc(vp, vn), cmp(vp, vn), cmp(vp, vc));
  return 0;
}
"""
INT32_WANT = '40006\n40006\n164476\n112772\n200012\n40001 -40001 -40000 80000 -60000 80000\n40001 40001 40001 32769 -40000\n26853 16384 40001 2 0\n'


@pytest.mark.parametrize("flags", [
    (), ("-O0",), ("--no-inlining",), ("--no-ast-optimize",),
    ("--no-licm", "--no-range-cmp", "--no-narrow-index")])
def test_int32_narrow_operand_chains(tmp_path, flags):
    """--int=32: a sum, a difference and other chains of operands that are
    narrower than int are computed in 32 bits, and so are a right shift,
    a quotient, a remainder, a unary + - ~ of an unsigned short."""
    assert build_and_run(tmp_path, INT32_SRC,
                         flags=("--int=32",) + flags) == INT32_WANT


# A static initializer with a minus in front of a constant expression held
# zero: ``int a = -(short)30000;`` had no initializer at all.
NEG_INIT_SRC = r"""#include <stdio.h>
#define US ((unsigned short)40000)
int a = -((short)30000);
int a2 = -(short)30000;
int a3 = -(-5);
int a4 = -(1 + 2);
int a5 = -(int)30000;
int a6 = -US;
int a7 = -(US >> 0);
int a8 = -(char)200;
long a9 = -(long)70000;
unsigned ua = -(unsigned short)1;
float fl = -(float)3;
int arr[] = { -(short)5, -(2 * 3), -US };
struct S { int x; long y; } st = { -(short)7, -(long)9 };
int main(void){ printf("%d %d %d %d %d %d %d %d %ld %u %d\n", a, a2, a3, a4, a5, a6, a7, a8, a9, ua, (int)fl);
 printf("%d %d %d %d %ld\n", arr[0], arr[1], arr[2], st.x, st.y); return 0; }
"""


def test_static_initializer_with_unary_minus_of_a_constant_expression(tmp_path):
    want32 = ("-30000 -30000 5 -3 -30000 -40000 -40000 56 -70000 "
              "4294967295 -3\n-5 -6 -40000 -7 -9\n")
    want16 = ("-30000 -30000 5 -3 -30000 25536 25536 56 -70000 "
              "65535 -3\n-5 -6 25536 -7 -9\n")
    assert build_and_run(tmp_path, NEG_INIT_SRC, flags=("--int=32",)) == want32
    assert build_and_run(tmp_path, NEG_INIT_SRC) == want16
