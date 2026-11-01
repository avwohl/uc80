"""Family J step 5c: x / k and x % k that the range of x decides
(docs/range_div.md).

Each program runs under cpmemu (with a timeout) and on the host compiler.
Both outputs must be the same.  The shape tests read UC80_RANGEDIV_DEBUG=1
output and the generated text.
"""

import os
import subprocess
import sys

import pytest

from .test_famj_kernels import ids, same_as_host
from .test_famj_startup import compile_c
from .test_struct_return_abi import REPO, _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

FLAGS = [
    (), ("--no-range-div",), ("-O0",), ("--no-register-locals",),
    ("--no-range-cmp",), ("--no-licm",),
]

SRC = r"""
#include <stdio.h>

static unsigned long seed = 74755UL;
static int next(void)
{
    seed = (seed * 1309UL + 13849UL) & 65535UL;
    return (int)(seed >= 32768UL ? (long)seed - 65536L : (long)seed);
}
static unsigned int unext(void) { return (next() & 0x7FFF) + 17u; }
static long big(void) { return (long)next() * 77L; }

volatile long vol = 99999L;
long gl;

/* the shape of Initarr: a long that holds an int */
static long fold1(void)
{
    long temp, s = 0;
    int i;
    for (i = 0; i < 40; i++) {
        temp = next();
        s += temp - (temp / 100000L) * 100000L - 50000L;
        s += temp % 100000L;
        s += temp / 32768L + temp % 32768L;
    }
    return s;
}

static long fold2(void)
{
    unsigned long u, s = 0;
    int i;
    for (i = 0; i < 40; i++) {
        u = unext();
        s += u / 65536UL + u % 65536UL + u / 40000UL + u % 40000UL;
    }
    return (long)s;
}

/* the range is too wide: no fold */
static long nofold1(void)
{
    long temp, s = 0;
    int i;
    for (i = 0; i < 40; i++) {
        temp = big();
        s += temp / 100000L + temp % 100000L;
        temp = next() * 4L;
        s += temp / 100000L + temp % 100000L;
    }
    return s;
}

/* a store that is not a plain assignment, an address, a volatile */
static long nofold2(void)
{
    long a = 0, b, c, s = 0;
    long *p = &c;
    int i;
    for (i = 0; i < 40; i++) {
        a += next();
        b = next();
        b++;
        c = next();
        *p = *p * 3L;
        s += a / 100000L + a % 100000L + b / 40000L + b % 40000L;
        s += c / 100000L + c % 100000L;
        s += vol / 100000L + vol % 100000L;
    }
    return s;
}

/* a parameter and a global are not tracked */
static long nofold3(long x)
{
    long s = x / 100000L + x % 100000L;
    gl = next();
    gl = gl * 5L;
    return s + gl / 100000L + gl % 100000L;
}

/* chars and shorts in a division */
static int small(void)
{
    unsigned char c = 200;
    signed char d = -100;
    short e = -30000;
    int s = 0, i;
    for (i = 0; i < 20; i++) {
        s += c / 300 + d / 200 + e / 40000 + c % 300 + d % 200 + e % 40000;
        c = (unsigned char)(c + 37);
        d = (signed char)(d + 11);
        e = (short)(e + 3001);
    }
    return s;
}

/* the boundary: k - 1, k, -k, -k + 1 */
static int edge(void)
{
    int s = 0, i;
    int v[8] = {99, 100, -99, -100, 0, 1, -1, 101};
    for (i = 0; i < 8; i++) {
        int x = v[i];
        long t = x;
        s += (int)(t / 100L) + (int)(t % 100L);
        s += x / 100 + x % 100;
    }
    return s;
}

/* ranges from constants: -99..99 folds for 100, -99..100 does not */
static int edge2(int sel)
{
    long t;
    if (sel)
        t = 99;
    else
        t = -99;
    return (int)(t / 100L) * 7 + (int)(t % 100L);
}

static int edge3(int sel)
{
    long t;
    if (sel)
        t = 100;
    else
        t = -99;
    return (int)(t / 100L) * 7 + (int)(t % 100L);
}

int main(void)
{
    printf("%d %d %d %d\n", edge2(0), edge2(1), edge3(0), edge3(1));
    printf("%ld\n", fold1());
    printf("%ld\n", fold2());
    printf("%ld\n", nofold1());
    printf("%ld\n", nofold2());
    { long a = nofold3(123456L); long b = nofold3(-250000L);
      printf("%ld %ld\n", a, b); }
    printf("%d\n", small());
    printf("%d\n", edge());
    return 0;
}
"""


@pytest.mark.parametrize("flags", FLAGS, ids=ids)
def test_range_div_against_host(tmp_path, flags):
    same_as_host(tmp_path, SRC, *flags)


def debug_lines(tmp_path, src, *flags):
    c_file = tmp_path / "d.c"
    c_file.write_text(src)
    env = dict(os.environ, UC80_RANGEDIV_DEBUG="1")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), *flags,
         "-o", str(tmp_path / "d.mac")],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=300)
    assert r.returncode == 0, r.stderr
    return [ln for ln in r.stderr.splitlines() if ln.startswith("rangediv:")]


def test_range_div_folds_where_the_range_decides(tmp_path):
    lines = debug_lines(tmp_path, SRC)
    assert any("fold1" in ln for ln in lines)
    assert any("fold2" in ln for ln in lines)
    assert any("small" in ln for ln in lines)
    assert any("edge2:" in ln for ln in lines)


@pytest.mark.parametrize("fn", ["nofold1", "nofold2", "nofold3", "edge3",
                                "edge:"])
def test_range_div_stays_out(tmp_path, fn):
    lines = debug_lines(tmp_path, SRC)
    key = fn if fn.endswith(":") else f"{fn}:"
    assert not any(key in ln for ln in lines), lines


def test_range_div_switch_off(tmp_path):
    assert not debug_lines(tmp_path, SRC, "--no-range-div")


def test_initarr_shape_has_no_division_call(tmp_path):
    src = r"""
extern int rand(void);
long list[20];
void fill(void)
{
    long temp;
    int i;
    for (i = 1; i < 20; i++) {
        temp = rand();
        list[i] = temp - (temp / 100000L) * 100000L - 50000L;
    }
}
int main(void) { fill(); return (int)list[3]; }
"""
    on = compile_c(tmp_path, src).read_text()
    off = compile_c(tmp_path, src, "--no-range-div", name="o").read_text()
    a = on.index("\n_fill:")
    f_on = on[a:on.index("@fill_ret:", a)]
    b = off.index("\n_fill:")
    f_off = off[b:off.index("@fill_ret:", b)]
    assert "__div32" not in f_on and "__sdiv32" not in f_on
    assert "div32" in f_off
