"""Type corner cases: the type of a character constant, C23 auto, an inlined
uint16_t parameter, a compound literal behind a local pointer, typeof of a
function type.

* sizeof('a') and typeof('a') are int (C23 6.4.4.5); the code generator
  keeps the constant as a char internally and sizeof said 1.
* `auto x = 1.5;` made an int: the declaration had no type specifier and
  the initializer was ignored.  The type is now the unqualified type of the
  initializer after array and function decay.
* An inlined function whose parameter is a uint16_t kept a decimal literal
  such as 40000 (a long) as the argument, so `x + x` was a 32-bit sum.
"""

import pytest

from tests.test_struct_return_abi import build_and_run  # noqa: F401
from tests.test_struct_return_abi import pytestmark  # noqa: F401

CONFIGS = [
    pytest.param((), id="default"),
    pytest.param(("--no-ast-optimize",), id="no-ast-optimize"),
    pytest.param(("--no-inlining",), id="no-inlining"),
]
PRINTF = ("--printf", "all")

CHAR_CONST = r"""#include <stdio.h>
int main(void) {
    printf("%d %d\n", (int)sizeof('a'), (int)sizeof(typeof('a')));
    typeof('a') x = 300;
    printf("%d\n", x);
    printf("%d\n", _Generic('a', char: 1, int: 2, default: 3));
    _Static_assert(sizeof('a') == sizeof(int), "char constant is an int");
    return 0;
}
"""


@pytest.mark.parametrize("flags", CONFIGS)
def test_character_constant_is_an_int(tmp_path, flags):
    out = build_and_run(tmp_path, CHAR_CONST, PRINTF + flags)
    assert out == "2 2\n300\n2\n", out


AUTO = r"""#include <stdio.h>
int main(void){
    auto a = 1.5;
    auto b = 1.5f;
    auto c = 1;
    auto d = 1L;
    auto e = 1u;
    auto f = 1ull;
    auto g = 'x';
    auto h = 1.5L;
    auto i = (short)3;
    auto j = (unsigned char)200;
    auto k = 3 > 2;
    auto l = "str";
    auto m = (float)2;
    auto n = 1.0 + 2;
    auto o = 2.5f * 2;
    auto p = (long long)5;
    auto q = &a;
    auto r = (const int)4;
    printf("%d %d %d %d %d %d %d %d\n", (int)sizeof a, (int)sizeof b, (int)sizeof c, (int)sizeof d, (int)sizeof e, (int)sizeof f, (int)sizeof g, (int)sizeof h);
    printf("%d %d %d %d %d %d %d %d %d %d\n", (int)sizeof i, (int)sizeof j, (int)sizeof k, (int)sizeof l, (int)sizeof m, (int)sizeof n, (int)sizeof o, (int)sizeof p, (int)sizeof q, (int)sizeof r);
    printf("%f %f %d %ld %u %d\n", a, b, c, d, e, g);
    printf("%f %f %f\n", h, n, o);
    printf("%d %d %f\n", i, j, m);
    printf("%d %s %lld %d\n", k, l, p, r);
    double aa = 2.5; auto s = aa * 2; printf("%f\n", s);
    auto t = 7 / 2.0; printf("%f %d\n", t, (int)sizeof t);
    return 0;
}
"""

AUTO_OUT = """4 4 2 4 2 8 2 4
2 1 2 2 4 4 4 8 2 2
1.500000 1.500000 1 1 1 120
1.500000 3.000000 5.000000
3 200 2.000000
1 str 5 4
5.000000
3.500000 4
"""


@pytest.mark.parametrize("flags", CONFIGS)
def test_auto_deduces_the_initializer_type(tmp_path, flags):
    out = build_and_run(tmp_path, AUTO, PRINTF + flags)
    assert out == AUTO_OUT, out


AUTO_MORE = r"""#include <stdio.h>
int arr[3] = {4, 5, 6};
struct S { int a; long b; };
int main(void) {
    auto p = arr;                 /* int * after decay */
    auto q = &arr[1];
    auto d = 2.5;
    const double cd = 7.5;
    auto e = cd;                  /* unqualified double */
    struct S s = {1, 2};
    auto t = s;
    auto u = s.b;
    for (auto x = 0.5; x < 2.0; x += 0.5) printf("%f ", x);
    p[1] = 9;
    printf("%d %d %f %f %d %ld %d\n", *q, (int)sizeof p, d, e, t.a, u,
           (int)sizeof t);
    return 0;
}
"""


@pytest.mark.parametrize("flags", CONFIGS)
def test_auto_with_arrays_structs_and_for(tmp_path, flags):
    out = build_and_run(tmp_path, AUTO_MORE, PRINTF + flags)
    assert out == "0.500000 1.000000 1.500000 9 2 2.500000 7.500000 1 2 6\n", out


INLINE16 = r"""#include <stdio.h>
#include <stdint.h>
volatile int vi = 7;
static long f1(uint16_t x) { return (uint16_t)(x * 2); }
static long f2(uint16_t x) { return (long)(x + x); }
static long f3(uint16_t x) { return x * 2UL; }
static long f4(uint16_t x) { return (long)(x - 70000L + 70000L); }
static int f5(uint16_t x) { return x * 2 < 1000; }
static int f6(uint16_t x) { return (x + 1) == 0; }
static long f7(uint16_t x) { return (long)(x << 1); }
static long f8(uint16_t x) { return -(long)x; }
static int f9(uint16_t x) { return (x ^ 0xFFFF) == 5; }
static int f10(uint16_t x) { return x + vi > 3; }
static long f11(uint16_t x) { return (long)(x + 1); }
static int f12(uint16_t x) { return (x >> 15) == 1; }
static uint16_t f13(uint16_t x) { return x + 0x8000; }
static int f14(unsigned x) { return (x + 1) == 0; }
int main(void) {
    printf("%ld %ld %ld %ld\n", f1(40000), f2(40000), f3(40000), f4(40000));
    printf("%d %d %ld %ld\n", f5(40000), f6(65535), f7(40000), f8(40000));
    printf("%d %d %ld %d\n", f9(65530), f10(65535), f11(65535), f12(40000));
    printf("%d %d\n", f13(40000), f14(65535));
    printf("%ld %ld\n", f1(0xA000), f2(0xA000));
    return 0;
}
"""


@pytest.mark.parametrize("flags", CONFIGS)
def test_inlined_uint16_parameter_with_a_decimal_literal(tmp_path, flags):
    out = build_and_run(tmp_path, INLINE16, PRINTF + flags)
    assert out == ("14464 14464 80000 40000\n0 1 14464 -40000\n"
                   "1 1 0 1\n7232 1\n16384 16384\n"), out


COMPOUND = r"""#include <stdio.h>
int sum(const int *p, int n){ int s=0; while(n--) s += *p++; return s; }
int f(int k){
    const int *p = (const int[]){1,2};
    return p[k];
}
int g(void){
    const int *p;
    p = (const int[]){5,6,7};
    return p[2];
}
int h(int k){
    const int *p = k ? (const int[]){1,2} : (const int[]){3,4};
    return p[1];
}
int main(void){
    int x = f(1);
    const char *s = (const char[]){'h','i',0};
    const int *q = (const int[]){1,2,3};
    int *r = (int[3]){4,5,6};
    printf("%d %d %d %s %d %d\n", x, g(), h(0), s, sum(q,3), sum(r,3));
    return 0;
}
"""


@pytest.mark.parametrize("flags", CONFIGS)
def test_local_pointer_to_a_compound_array_literal(tmp_path, flags):
    out = build_and_run(tmp_path, COMPOUND, PRINTF + flags)
    assert out == "2 7 4 hi 6 15\n", out


TYPEOF_FN = r"""#include <stdio.h>
typeof(int (void)) f;
int f(void) { return 7; }
typeof(int (int, int)) add;
int add(int a, int b) { return a + b; }
int main(void) {
    typeof(int (void)) *fp = f;
    typeof(add) *ap = add;
    printf("%d %d\n", fp(), ap(2, 3));
    return 0;
}
"""


def test_typeof_of_a_function_type(tmp_path):
    out = build_and_run(tmp_path, TYPEOF_FN, PRINTF)
    assert out == "7 5\n", out


INLINE16_SMALL = r"""#include <stdio.h>
#include <stdint.h>
static long f(uint16_t x) { return (long)(-x); }
static long g(uint16_t x) { return (long)(~x); }
static long h(uint16_t x) { return (long)(2 - x); }
static int k(uint16_t x) { return (-x) > 100; }
static long m(uint16_t x) { return (long)(x - 1); }
int main(void) {
    printf("%ld %ld %ld %d\n", f(300), g(300), h(300), k(300));
    printf("%ld %ld %ld\n", f(32767), f(sizeof(int)), m(0));
    printf("%ld %ld\n", f(300u), f(0));
    return 0;
}
"""


@pytest.mark.parametrize("flags", CONFIGS)
def test_inlined_uint16_parameter_with_a_small_literal(tmp_path, flags):
    # A plain decimal literal is a signed int: it must be converted to the
    # unsigned parameter type before -x, ~x or a comparison sees it.
    out = build_and_run(tmp_path, INLINE16_SMALL, PRINTF + flags)
    assert out == ("65236 65235 65238 1\n32769 65534 65535\n"
                   "65236 0\n"), out


AUTO_SHAPES = r"""#include <stdio.h>
int fn(void) { return 5; }
struct S { char c; long l; } gm;
long gl;
int main(void) {
    auto s = "str";
    auto s2 = "ye" "s";
    printf("%c %c %c %d\n", s[0], s[1], *(s + 2), (int)sizeof(*s));
    s2++;
    printf("%c %c\n", *s2, s2[1]);
    auto fp = fn;
    printf("%d\n", fp());
    auto x1 = 1.5, x2 = 2.5;
    printf("%d\n", (int)(x1 + x2));
    auto i1 = 100000L, i2 = 7L, i3 = i1 / i2;
    printf("%d %d\n", (int)i3, (int)sizeof i2);
    auto c1 = 'a', c2 = 1.5f;
    printf("%d %d\n", (int)sizeof c1, (int)sizeof c2);
    return 0;
}
"""


@pytest.mark.parametrize("flags", CONFIGS)
def test_auto_string_function_and_several_declarators(tmp_path, flags):
    out = build_and_run(tmp_path, AUTO_SHAPES, PRINTF + flags)
    assert out == "s t r 1\ne s\n5\n4\n14285 4\n2 4\n", out


SIZEOF_TYPEOF = r"""#include <stdio.h>
struct M { char c; long l; } gm;
long gl;
int main(void) {
    long ll;
    printf("%d %d %d %d\n", (int)sizeof(typeof(gl)), (int)sizeof(typeof(gm)),
           (int)sizeof(typeof(gm.l)), (int)sizeof(typeof(1L)));
    printf("%d %d\n", (int)sizeof(typeof(ll)), (int)sizeof(typeof(ll) *));
    printf("%d %d %d\n", (int)sizeof('a') == 2,
           (int)sizeof 'a' == (int)sizeof(int),
           (long)sizeof('a') == (long)sizeof(int));
    int a = (int)sizeof('a');
    printf("%d\n", a);
    return 0;
}
"""


@pytest.mark.parametrize("flags", CONFIGS)
def test_sizeof_of_typeof_and_char_constant_in_a_comparison(tmp_path, flags):
    out = build_and_run(tmp_path, SIZEOF_TYPEOF, PRINTF + flags)
    assert out == "4 5 4 4\n4 2\n1 1 1\n2\n", out


ROUND = r"""#include <stdio.h>
#include <math.h>
int main(void) {
    printf("%ld %ld %ld %ld\n", lround(2.5), lround(-2.5), lround(0.4),
           lround(123456.5));
    printf("%lld %lld %lld\n", llround(1e10), llround(-1e10), llround(-0.5));
    printf("%ld %ld\n", lround(1e30), lround(-1e30));
    printf("%lld %lld\n", llround(1e30), llround(-1e30));
    return 0;
}
"""


def test_lround_and_llround_saturate(tmp_path):
    out = build_and_run(tmp_path, ROUND, PRINTF)
    assert out == ("3 -3 0 123457\n10000000000 -10000000000 -1\n"
                   "2147483647 -2147483648\n"
                   "9223372036854775807 -9223372036854775808\n"), out
