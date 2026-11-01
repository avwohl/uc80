"""The BENCH_CHECK macros of bench/bench.h (self-check of a time build).

For uc80 they expand to `do ; while (...)`.  A `while` spin loop is a
candidate of the loop rotation (docs/loop_rotation.md), and the copies of
the tests share a budget of 2 percent of the code: the spin loops of the
checks, which never run, took the budget from the loops of the program
(dhrystone lost the rotation of a loop in Proc_8 this way).  A check that
holds still passes, and the program goes on.
"""

import pytest

from .test_famj_kernels import run_raw
from .test_famj_startup import compile_c, REPO
from .test_struct_return_abi import _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

SRC = r"""
#include <stdio.h>
#include "bench.h"

unsigned g;
int main(void)
{
    unsigned i;
    for (i = 0; i < 100; i++)
        g += i;
    BENCH_CHECK_EQ(g, 4950);
    BENCH_CHECK_NEAR(g, 4951, 2);
    BENCH_CHECK(g == 4950);
    printf("ok %u\n", g);
    return 0;
}
"""


def test_check_loops_do_not_take_the_rotation_budget(tmp_path):
    mac = compile_c(tmp_path, SRC, f"-I{REPO / 'bench'}")
    text = mac.read_text()
    # one rotated loop: the loop of the program, none of the checks
    assert text.count("@RB") == 2, text.count("@RB")   # label + jump
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out == "ok 4950\n"
