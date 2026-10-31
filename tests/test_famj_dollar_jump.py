"""A ``jr cc,$+k`` that skips one instruction must keep the size of that
instruction.  The dead-code pass of the byte registers deleted the
``inc L`` after ``jr NZ,$+3`` when only H was read afterwards, and the jump
then skipped ``ld A,H`` (difftest seed 7111).
"""

import pytest

from .test_famj_kernels import ids, same_as_host
from .test_struct_return_abi import _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

SRC = r"""
#include <stdio.h>
static unsigned int g7 = 2u;
static unsigned char m3 = 158;
static unsigned char m4 = 2;
static unsigned long acc;

static void ck(unsigned long v) { acc = (((acc << 5) | (acc >> 27)) ^ v) & 0xFFFFFFFFul; }

static void pr(unsigned int q)
{
    unsigned int l = (unsigned int)(q != 1);
    unsigned int i = 2;
    if ((!(g7 - m3)) >= 256u) {
        l++;
        ck(l);
    }
    if ((!(g7 - m4)) >= 1u)
        ck(77);
    while (i) {
        --m3;
        ck(m3);
        i--;
    }
    ck(i);
    ck(l);
}

int main(void)
{
    pr(1);
    pr(5);
    g7 = 158;
    m3 = 158;
    pr(0);
    printf("%lu\n", acc);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [
    (), ("--no-byte-regs",), ("--no-register-locals",), ("-O0",),
], ids=ids)
def test_skip_over_one_instruction(tmp_path, flags):
    same_as_host(tmp_path, SRC, *flags)


def test_the_guard_sees_a_changed_landing():
    from uc80 import reglocals
    env = reglocals.Env(lambda t: reglocals.ALL7, lambda t: reglocals.ALL7)
    a = reglocals.parse_lenient(
        ["\tjr\tNZ,$+3", "\tinc\tL", "\tld\tA,H", "\tret"], env)
    b = reglocals.parse_lenient(["\tjr\tNZ,$+3", "\tld\tA,H", "\tret"], env)
    assert reglocals._dollar_landings(a) == ["ld"]
    assert reglocals._dollar_landings(b) == ["?"] or \
        reglocals._dollar_landings(b) != ["ld"]
