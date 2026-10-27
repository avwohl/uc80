"""Register locals (BC, DE, IY) together with the code generator fixes.

The register-locals pass rewrites the text that the code generator makes.
These programs use the sequences that the bugfix branch changed (16-bit
negation that keeps DE, the float compare with its NaN test, inlined
uint16_t literal arguments, long long values) in functions that keep
locals in BC, DE and IY.  The output must be the same with the pass on
and off, and must match the expected text.
"""

import re

import pytest

from .test_register_cc import compile_mac, needs_run
from .test_register_locals import MODES, body_of, build_run

PROG = r"""
#include <stdio.h>
#include <stdint.h>
struct P { int x; int y; struct P *next; };
static int neg_loop(int n) {
    int a = 0, b = 100;
    for (int i = 0; i < n; i++) { a += -i; b -= -(a & 7); }
    return a * 3 + b;
}
static int fcount(float *v, int n) {
    int lt = 0, ne = 0;
    for (int i = 0; i < n; i++) {
        if (v[i] < 1.0f) lt++;
        if (v[i] != v[i]) ne++;
        if (v[i] == v[i]) ne += 10;
    }
    return lt * 100 + ne;
}
static uint16_t add16(uint16_t a, uint16_t b) { return a + b; }
static long lits(int n) {
    long s = 0;
    for (int i = 0; i < n; i++) s += add16(65530, (uint16_t)i);
    return s;
}
static int walk(struct P *p) {
    int t = 0;
    while (p) { t += -p->x + p->y; p = p->next; }
    return t;
}
static long long wide(int n) {
    long long w = 3000000000LL;
    for (int i = 0; i < n; i++) w = -w + i;
    return w;
}
int main(void) {
    float nan = 0.0f / 0.0f;
    float v[4] = {0.5f, nan, 2.0f, -1.0f};
    struct P c = {5, 9, 0}, b = {-2, 4, &c}, a = {7, -1, &b};
    printf("%d\n", neg_loop(9));
    printf("%d\n", fcount(v, 4));
    printf("%ld\n", lits(5));
    printf("%d\n", walk(&a));
    printf("%lld\n", wide(5));
    return 0;
}
"""


EXPECT = ["24", "231", "327660", "2", "-2999999998"]


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_same_output(tmp_path, flags):
    assert build_run(tmp_path, PROG, *flags).split() == EXPECT


def _mem(text):
    return len(re.findall(r"\(\?\?AUTO\+\d+\)|\(IX[+-]\d+\)|\(IY[+-]\d+\)", text))


def test_pass_acts_and_sequences_stay(tmp_path):
    """Each function keeps its bugfix sequence and has fewer memory accesses."""
    on = compile_mac(tmp_path, PROG)[0]
    off = compile_mac(tmp_path, PROG, "--no-register-locals")[0]
    for name in ("neg_loop", "fcount", "lits", "walk", "wide"):
        assert _mem(body_of(on, name)) < _mem(body_of(off, name)), name
    assert "sbc\tA,A" in body_of(on, "neg_loop")
    assert "sbc\tA,A" in body_of(on, "walk")
    assert "__fcmp" in body_of(on, "fcount")
    assert "IY" in body_of(on, "walk")
