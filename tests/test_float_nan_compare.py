"""Float comparisons with a NaN follow IEEE 754 (C23 Annex F.9.3).

A comparison with a NaN operand is unordered: `==`, `<`, `<=`, `>` and `>=`
give 0 and `!=` gives 1.  The code generator read only Z and C from __fcmp,
so `n == n` was true, `n != n` false and `n > 1` could be true.  __fcmp now
returns Z and C both set for an unordered pair (a pair the ordered compare
never gives), and the code after the call tests that pair for each operator.
The expected text of NAN_MATRIX was produced by the host C compiler.
"""

import pytest

from tests.test_struct_return_abi import build_and_run  # noqa: F401
from tests.test_struct_return_abi import pytestmark  # noqa: F401

CONFIGS = [
    pytest.param((), id="default"),
    pytest.param(("-O0",), id="no-peephole"),
    pytest.param(("--no-ast-optimize",), id="no-ast-optimize"),
]
PRINTF = ("--printf", "all")

NAN_MATRIX = r"""#include <stdio.h>
#include <math.h>
volatile double vn = NAN;
volatile double v1 = 1.0;
volatile int vi = 3;
double nn(void) { return vn; }
int main(void) {
    double n = vn, one = v1;
    int i = vi;
    float fn = (float)n;
    printf("c: %d %d %d %d %d %d\n", n == 0.0, n != 0.0, n < 0.0, n > 0.0, n <= 0.0, n >= 0.0);
    printf("c: %d %d %d %d %d %d\n", 0.0 == n, 0.0 != n, 0.0 < n, 0.0 > n, 0.0 <= n, 0.0 >= n);
    printf("i: %d %d %d %d %d %d\n", n == i, n != i, n < i, n > i, n <= i, n >= i);
    printf("i: %d %d %d %d %d %d\n", i == n, i != n, i < n, i > n, i <= n, i >= n);
    printf("f: %d %d %d %d\n", fn == n, fn != n, fn < n, fn >= n);
    printf("call: %d %d %d %d %d %d\n", nn() == nn(), nn() != nn(), nn() < 1, nn() > 1, nn() <= 1, nn() >= 1);
    printf("neg: %d %d %d %d\n", !(n < one), !(n >= one), !(n == one), !(n != one));
    printf("neg: %d %d\n", (n < one) == 0, (n > one) != 1);
    printf("logic: %d %d %d %d\n", (n < one) || (n > one), (n < one) && 1, (n == n) || (n != n), !n);
    int k = 0;
    if (!(n < one)) k |= 1;
    if (!(n >= one)) k |= 2;
    if (n < one || n >= one) k |= 4;
    if (n != n && n != n) k |= 8;
    printf("k=%d\n", k);
    k = 0; do { k++; } while (n < one && k < 4); printf("k=%d\n", k);
    k = 0; for (; n > one || n < one; ) { k = 9; break; } printf("k=%d\n", k);
    printf("tern: %d %d\n", n >= one ? 1 : 2, n < one ? 1 : 2);
    printf("sel: %d\n", (n <= one) + (n >= one) + (n < one) + (n > one) + (n == n));
    double arr[2] = {NAN, 1.0}; 
    printf("arr: %d %d\n", arr[0] == arr[0], arr[1] == arr[1]);
    double mn = one; double mx = one;
    if (n < mn) mn = n; if (n > mx) mx = n;
    printf("mm: %f %f\n", mn, mx);
    printf("fmax: %f %f\n", fmax(n, one), fmin(one, n));
    printf("%d %d\n", isnan(n), isnan(one));
    printf("cmp1: %d %d %d %d %d\n", one < 2.0, one <= 1.0, one > 0.5, one >= 1.0, one == 1.0);
    printf("cmp2: %d %d %d %d %d\n", one > 2.0, one < 1.0, one < 0.5, one <= 0.5, one != 1.0);
    printf("big: %d %d\n", 3.0e38 < 1e38, 3.0e38 > 1e38);
    return 0;
}
"""

NAN_MATRIX_OUT = """c: 0 1 0 0 0 0
c: 0 1 0 0 0 0
i: 0 1 0 0 0 0
i: 0 1 0 0 0 0
f: 0 1 0 0
call: 0 1 0 0 0 0
neg: 1 1 1 0
neg: 1 1
logic: 0 0 1 0
k=11
k=1
k=0
tern: 2 2
sel: 0
arr: 0 1
mm: 1.000000 1.000000
fmax: 1.000000 1.000000
1 0
cmp1: 1 1 1 1 1
cmp2: 0 0 0 0 0
big: 0 1
"""

NAN_BASIC = r"""#include <stdio.h>
#include <math.h>
volatile double vn = NAN;
volatile double v1 = 1.0;
volatile float fn = NAN;
volatile float f1 = 1.0f;
int main(void) {
    double n = vn, one = v1;
    float m = fn, fone = f1;
    printf("d: %d %d %d %d %d %d\n", n == n, n != n, n < one, n > one, n <= one, n >= one);
    printf("d: %d %d %d %d %d %d\n", one == n, one != n, one < n, one > n, one <= n, one >= n);
    printf("f: %d %d %d %d %d %d\n", m == m, m != m, m < fone, m > fone, m <= fone, m >= fone);
    printf("f: %d %d %d %d %d %d\n", fone == m, fone != m, fone < m, fone > m, fone <= m, fone >= m);
    printf("n: %d %d %d\n", !n, n && 1, n || 0);
    printf("t: %d\n", n < one ? 1 : 2);
    if (n < one) printf("BAD if<\n"); else printf("ok if<\n");
    if (n == n) printf("BAD if==\n"); else printf("ok if==\n");
    if (n != n) printf("ok if!=\n"); else printf("BAD if!=\n");
    if (n > one) printf("BAD if>\n");
    if (n <= one) printf("BAD if<=\n");
    if (n >= one) printf("BAD if>=\n");
    if (n) printf("ok truth\n"); else printf("BAD truth\n");
    int k = 0; while (n < one && k < 3) k++; printf("w %d\n", k);
    for (k = 0; n >= one && k < 3; k++); printf("f %d\n", k);
    printf("o: %d %d %d %d\n", one == one, one != one, one < one, one <= one);
    printf("o: %d %d\n", one < 2.0, one >= 2.0);
    printf("i: %d %d\n", -one < one, -one > one);
    printf("z: %d %d\n", 0.0 == -0.0, (double)0 < one);
    double inf = INFINITY;
    printf("inf: %d %d %d %d %d\n", inf == inf, inf > one, -inf < one, inf < one, inf != inf);
    return 0;
}
"""

NAN_BASIC_OUT = """d: 0 1 0 0 0 0
d: 0 1 0 0 0 0
f: 0 1 0 0 0 0
f: 0 1 0 0 0 0
n: 0 1 1
t: 2
ok if<
ok if==
ok if!=
ok truth
w 0
f 0
o: 1 0 0 1
o: 1 0
i: 1 0
z: 1 1
inf: 1 1 1 0 0
"""

NAN_CONST = r"""#include <stdio.h>
#include <math.h>
static int s1 = (NAN == NAN), s2 = (NAN != NAN), s3 = (NAN < 1.0), s4 = (NAN >= 1.0), s5 = !(NAN < 1.0), s6 = (INFINITY > 1e30);
_Static_assert(NAN != NAN, "nan");
_Static_assert(!(NAN == NAN), "nan");
_Static_assert(!(NAN < 0.0f) , "nan");
int main(void) {
    printf("%d %d %d %d %d %d\n", s1, s2, s3, s4, s5, s6);
    printf("%d %d %d %d\n", NAN == NAN, NAN != NAN, NAN < 1.0, !(NAN >= 1.0));
    printf("%d %d\n", (int)(NAN == NAN) + 5, 1 ? NAN != NAN : 0);
    return 0;
}
"""

NAN_CONST_OUT = """0 1 0 0 1 1
0 1 0 1
5 1
"""


@pytest.mark.parametrize("flags", CONFIGS)
def test_nan_matrix(tmp_path, flags):
    out = build_and_run(tmp_path, NAN_MATRIX, PRINTF + flags)
    assert out == NAN_MATRIX_OUT, out


@pytest.mark.parametrize("flags", CONFIGS)
def test_nan_in_every_condition_context(tmp_path, flags):
    out = build_and_run(tmp_path, NAN_BASIC, PRINTF + flags)
    assert out == NAN_BASIC_OUT, out


@pytest.mark.parametrize("flags", CONFIGS)
def test_nan_constants(tmp_path, flags):
    out = build_and_run(tmp_path, NAN_CONST, PRINTF + flags)
    assert out == NAN_CONST_OUT, out
