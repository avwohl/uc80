"""Merge of family L into the integrate14 repair tree: interactions that
RUN.  The expected output is the output of the host compiler.

1. A divide pair (L4, __sdivmod16 and the unsigned form) in a loop or in a
   function other than main keeps the fast __div16 (no compact __div16).

The regcall pragma with the bug round 7 pragma handling is covered by
tests/test_lb_codegen.py (a normal compile) and tests/test_bugfix7_core.py.
"""

import re

import pytest

from .test_integ14_famk_bug6 import host, uc

PAIR_S = r"""
#include <stdio.h>
int a[8];
int main(void)
{
    int n = 7, x = 31000, i;
    for (i = 0; i < 6; i++) {
        a[i] = x % n;
        x = 10 * a[i] + x / n + i;
    }
    printf("%d %d %d\n", x, a[0], a[5]);
    return 0;
}
"""
PAIR_U = PAIR_S.replace("int a[8]", "unsigned a[8]").replace(
    "int n = 7, x = 31000, i;", "unsigned n = 700, x = 61000; int i;").replace(
    "%d %d %d", "%u %u %u")


@pytest.mark.parametrize("src", [PAIR_S, PAIR_U], ids=["signed", "unsigned"])
def test_divmod_pair_in_loop_keeps_fast_div16(tmp_path, src):
    want = host(tmp_path, src)
    out, mac = uc(tmp_path, src)
    assert out == want
    assert re.search(r"EXTRN\s+__dv8w", mac)
    out, _mac = uc(tmp_path, src, "--no-divmod-pair")
    assert out == want


IDX_SRC = r"""
#include <stdio.h>
int main(void)
{
    int a[200], x = 0, n, N = 200;
    for (n = 1; n < 200; n++) a[n] = 1;
    a[0] = 0;
    while (N > 190) {
        n = N--;
        while (--n) {
            a[n] = x % n;
            x = 10 * a[n - 1] + x / n;
        }
        printf("%d ", x);
    }
    printf("\n");
    return 0;
}
"""


def test_divisor_used_as_array_index_is_small(tmp_path):
    """The divisor n indexes a[200]: under 256, so a pair in a loop may use
    the compact __div16 (no __dv8w).  The output stays right."""
    want = host(tmp_path, IDX_SRC)
    out, mac = uc(tmp_path, IDX_SRC)
    assert out == want
    assert re.search(r"EXTRN\s+__dv8w", mac) is None
    out, mac = uc(tmp_path, IDX_SRC, "--no-div16-compact")
    assert out == want
