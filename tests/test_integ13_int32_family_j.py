"""--int 32 results of 16-bit operands (bug round 6) with the family J
passes: range division, byte compares, loop-invariant code, byte registers.

Under --int 32 a quotient or a right shift of a 16-bit operand is an int of
32 bits.  The range division does not fold under --int 32 (rangediv.fold
returns None for int_size 4), so the two features must not meet.  The
expected values are fixed, so the test needs no host compiler.
"""

import pytest

from .test_famj_kernels import run_raw, ids
from .test_famj_startup import compile_c
from .test_struct_return_abi import _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

SRC = r"""
#include <stdio.h>

unsigned short us = 40000;
short s = -32768, m1 = -1;
int n = 0;

int main(void)
{
    unsigned char b = 200;
    int i, sum = 0, cnt = 0;
    unsigned char k;
    printf("%d %d %d %d\n", (us >> n) + 1, s / m1, -(s >> 1), ~(us >> n));
    printf("%d %d %d\n", b / 3, b % 7, (b / 3) * 1000);
    for (i = 0; i < 3; i++)
        sum += (us >> n) + i + us / (n + 1);
    for (k = 0; k < 250; k++)
        if (k / 5 > 40)
            cnt++;
    printf("%d %d\n", sum, cnt);
    return 0;
}
"""

EXPECT = "40001 32768 16384 -40001\n66 4 66000\n240003 45\n"


@pytest.mark.parametrize(
    "flags", [(), ("--no-range-div",), ("-O0",),
              ("--no-byte-regs", "--no-licm", "--no-range-byte-cmp")],
    ids=ids)
def test_int32_quotients_and_family_j(tmp_path, flags):
    mac = compile_c(tmp_path, SRC, "--int=32", *flags)
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out == EXPECT


RD_SRC = r"""
#include <stdio.h>

int big = 200000;                  /* an int of 32 bits under --int=32 */
int rnd(void) { return big; }

int main(void)
{
    long temp;
    temp = rnd();                  /* the case of docs/range_div.md */
    printf("%d %ld %ld %ld\n", (int)sizeof(int), temp / 100000L,
           temp % 100000L, temp - (temp / 100000L) * 100000L - 50000L);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("-O0",)], ids=ids)
def test_range_division_does_not_fold_for_32_bit_int(tmp_path, flags):
    """With a 16-bit int the range of `rnd()` decides `temp / 100000L`
    (0).  With --int=32 the range is 32 bits wide, and the fold must stay
    off: the quotient is 2.  A change of the guard in rangediv.fold fails
    this test."""
    mac = compile_c(tmp_path, RD_SRC, "--int=32", *flags)
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out == "4 2 0 -50000\n"
