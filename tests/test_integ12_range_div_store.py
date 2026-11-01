"""Range division (family J) and the conversion in a store.

A store to a local of a narrow type wraps the value into the type, so the
range of the stored expression is not the range of the local.  Found by
difftest seed 9574: a plain char that holds 0xC5 (-59) was divided by
2147483648UL as unsigned long (0xFFFFFFC5 / 0x80000000 = 1), and the
pass folded the division to 0.  The expected values are fixed 32-bit
results, so the test needs no host compiler.
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

signed char g6 = 5;
static unsigned int f28(unsigned int a, unsigned long b) { return 1; }

int main(void)
{
    char c = (f28(0, g6) * (g6 - 0x40));
    signed char sc;
    unsigned char uc;
    unsigned long a, b, d, e;
    sc = (signed char) 65477u;
    uc = 65477u;
    a = c / 2147483648UL;
    b = sc / 2147483648UL;
    d = uc / 2147483648UL;
    e = (unsigned long) uc % 100000UL;
    printf("%lu %lu %lu %lu\n", a, b, d, e);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("--no-range-div",), ("-O0",)],
                         ids=ids)
def test_a_narrow_local_wraps_before_the_division(tmp_path, flags):
    mac = compile_c(tmp_path, SRC, *flags)
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out == "1 1 0 197\n"
