"""Infinity and NaN: the values math.h hands out, and how printf shows them.

`INFINITY` was `((double)0x7FFFFFFF)` and `NAN` was `((double)0)`, so
`isinf(INFINITY)` was false and `NAN` compared equal to zero -- both silent.
They are now folded from the GCC builtins, which makes them real constant
expressions with the right bit patterns, usable in a static initializer.

A literal too large for binary32 used to stop the compiler with "internal
error: float too large to pack with f format"; C23 6.3.1.5p2 says it
becomes an infinity.

And `%f` printed a NaN as `inf`: it tested only the exponent, and it tested
it after a rounding pass that does not preserve the mantissa telling the two
apart.  `%F` printed lowercase where C17 7.21.6.1p8 asks for `INF` / `NAN`.
"""

import pytest

from tests.test_struct_return_abi import build_and_run  # noqa: F401
from tests.test_struct_return_abi import pytestmark  # noqa: F401


MACROS = """#include <stdio.h>
#include <math.h>
static double si = INFINITY;
static double sn = NAN;
int main(void) {
    double i = INFINITY, n = NAN;
    printf("%d %d %d %d\\n", isinf(i), isnan(n), isinf(si), isnan(sn));
    printf("%d\\n", n == 0.0);
    return 0;
}
"""


def test_infinity_and_nan_are_what_they_claim(tmp_path):
    out = build_and_run(tmp_path, MACROS, ("--printf", "int"))
    assert "1 1 1 1\n" in out, out
    # NAN used to be 0.0, so this compared equal.  (uc80's float compare
    # does not implement NaN's unordered result -- `n == n` is still true.
    # Recorded in todo.txt; the runtime never produces a NaN by itself.)
    assert "0\n" in out, out


OVERFLOW = """#include <stdio.h>
#include <math.h>
static double s = 1e40;
int main(void) {
    double a = 1e40, b = -1e40;
    printf("%d %d %d\\n", isinf(a), isinf(b), isinf(s));
    return 0;
}
"""


def test_a_literal_too_large_for_a_float_becomes_an_infinity(tmp_path):
    out = build_and_run(tmp_path, OVERFLOW, ("--printf", "int"))
    assert "1 1 1\n" in out, out


PRINTING = """#include <stdio.h>
#include <math.h>
int main(void) {
    double n = NAN, i = INFINITY, m = -INFINITY;
    printf("[%f][%F][%e][%g]\\n", n, n, n, n);
    printf("[%f][%F][%e][%g]\\n", i, i, i, i);
    printf("[%f][%F]\\n", m, m);
    return 0;
}
"""


def test_printf_names_an_infinity_and_a_nan_apart(tmp_path):
    out = build_and_run(tmp_path, PRINTING, ("--printf", "float"))
    assert "[nan][NAN][nan][nan]\n" in out, out
    assert "[inf][INF][inf][inf]\n" in out, out
    assert "[-inf][-INF]\n" in out, out


DETECTION = """#include <stdio.h>
int under_default(int n) {
    switch (n) {
    default:
        printf("[%f]\\n", 1.5);
    }
    return 0;
}
int in_an_initializer(void) {
    int a[2] = { printf("[%f]\\n", 2.5), 0 };
    return a[1];
}
int main(void) { under_default(0); in_an_initializer(); return 0; }
"""


def test_a_printf_anywhere_registers_its_conversions(tmp_path):
    """Auto-detection decides which handlers the dispatch table carries.  A
    printf it cannot see leaves the conversion to be echoed verbatim."""
    out = build_and_run(tmp_path, DETECTION)
    assert "[1.500000]\n" in out, out
    assert "[2.500000]\n" in out, out
