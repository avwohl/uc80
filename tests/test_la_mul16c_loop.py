"""L5: a multiplication in a loop with calls in it uses __mul16c
(docs/family_l.md); a loop with no calls keeps the fast __mul16."""

from ._faml import needs_toolchain, run_both

pytestmark = needs_toolchain

LIGHT = r"""
#include <stdio.h>
static unsigned g1, g2, g3;
static void a(void) { g1++; }
static void b(void) { g2 += 2; }
static void c(void) { g3 ^= 1; }
int main(void)
{
    volatile unsigned m = 7, k = 5;
    unsigned s = 0, i;
    for (i = 0; i < 300; i++) {
        a(); b(); c();
        s += i * m;
        s += (i ^ k) * k;
    }
    printf("%u %u %u %u\n", s & 0xffff, g1, g2, g3);
    return 0;
}
"""

CALLS = r"""
#include <stdio.h>
static unsigned g;
static void f1(unsigned a) { unsigned t; for (t = 0; t < (a & 7); t++) g += a; g ^= 1; }
static unsigned f2(unsigned a) { unsigned t = a ^ g; return t + 1; }
static void f3(unsigned a) { f1(a); g -= a; g |= 2; }
int main(void)
{
    unsigned i, j = 7, k = 3, s = 0;
    for (i = 0; i < 40; i++) {
        f1(i);
        s += f2(i);
        f3(j);
        f1(k);
        s += (i - 20) * (j + k);
        j = (j * 5 + 1) & 0xff;
        k = i * 31 - 40;
    }
    printf("%u %u %u\n", s & 0xffff, g & 0xffff, (j * k) & 0xffff);
    return 0;
}
"""

NOCALLS = r"""
#include <stdio.h>
int main(void)
{
    unsigned i, j = 7, k = 3, s = 0;
    for (i = 0; i < 40; i++) {
        s += (i - 20) * (j + k);
        j = (j * 5 + 1) & 0xff;
        k = i * 31 - 40;
    }
    printf("%u %u\n", s & 0xffff, (j * k) & 0xffff);
    return 0;
}
"""


def test_loop_with_calls_uses_the_short_multiply(tmp_path):
    want, got, text = run_both(tmp_path, CALLS)
    assert got == want
    assert "call\t__mul16c" in text
    assert "call\t__mul16\n" not in text


def test_option_keeps_the_fast_multiply(tmp_path):
    want, got, text = run_both(tmp_path, CALLS, "--no-mul16c-loop")
    assert got == want
    assert "call\t__mul16c" not in text


def test_loop_without_calls_keeps_the_fast_multiply(tmp_path):
    want, got, text = run_both(tmp_path, NOCALLS)
    assert got == want
    assert "call\t__mul16c" not in text
    assert "call\t__mul16" in text


def test_loop_of_light_calls_keeps_the_fast_multiply(tmp_path):
    # three calls of leaf functions cost less than the slow multiply
    want, got, text = run_both(tmp_path, LIGHT)
    assert got == want
    assert "call\t__mul16c" not in text
