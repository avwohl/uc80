"""Index narrowing (docs/narrow_index.md).

A 32-bit index that only reaches a 16-bit address is computed in 16 bits.
The programs run in every mode and must print the same text.  The
expected text comes from a host build for the programs whose indexes stay
inside their arrays, and from the numbers in the comments for the ones
that wrap.
"""

import os
import subprocess
import sys

import pytest

from .test_register_cc import compile_mac, needs_run, run_mac
from .test_struct_return_abi import REPO

MODES = [
    (), ("--no-narrow-index",), ("-O0",), ("--no-register-locals",),
    ("--no-licm",), ("--no-inlining",), ("--int", "32"),
]

SRC = r"""
#include <stdio.h>
#include <stdint.h>

int m[36];
int16_t h[40];
struct S { int pad; int tab[8]; } st[3];
volatile uint32_t vk = 2;
uint32_t cnt;

static void init(void)
{
    int i;
    for (i = 0; i < 36; i++) m[i] = i * 3 + 1;
    for (i = 0; i < 40; i++) h[i] = i * 2;
    for (i = 0; i < 3; i++) for (cnt = 0; cnt < 8; cnt++) st[i].tab[cnt] = i * 10 + cnt;
    cnt = 0;
}

static int matrix(uint32_t n)
{
    uint32_t i, j, k;
    int s = 0;
    for (i = 0; i < n; i++)
        for (j = 0; j < n; j++)
            for (k = 0; k < n; k++)
                s += m[i * n + j] * (int)k + m[k * n + j];
    return s;
}

static int shifts(uint32_t i, uint32_t j)
{
    return m[(i << 2) + j] + m[(i << 1) - j + 8] + h[(i * 5 + j * 3) & 31];
}

static int signedidx(int32_t i, int32_t j)
{
    return m[i * 3 + j + 20] + m[j - i + 10];
}

static int negconst(uint32_t i)
{
    return m[i * 2 - 1] + m[20 - i * 3] + m[-i + 12];
}

static int bitops(uint32_t i)
{
    return m[(i & 3) | 8] + m[(i ^ 5) & 7] + m[~(~i & 15) & 7];
}

/* the right shift and the division and the modulo read bits above 15 */
static int highbits(uint32_t x)
{
    return h[(x >> 16) + 1] + h[(x / 3) % 40] + m[(x % 7) * 2 + 1];
}

static int fromchars(unsigned char a, signed char b)
{
    return m[a * 3 + b + 5] + m[(uint32_t)a * b + 20];
}

static int sidefx(void)
{
    uint32_t k = 0;
    int s = 0;
    s += m[k++ * 3 + 1];
    s += m[k++ * 3 + 1];
    s += m[vk * 3 + vk];
    return s * 100 + (int)k;
}

static int ptrs(uint32_t n)
{
    int *p = &m[10];
    int *q = p + n * 2;
    int *r = n * 3 + p;
    int *t = q - n;
    p += n + 1;
    q -= n * 2;
    return *p + *q + *r + *t + (int)(r - q) * 100 + (int)(r - p);
}

static int structs(uint32_t i, uint32_t j)
{
    return st[i].tab[j * 2 + i] + st[i + 1].tab[j * 3] + st[1].tab[(i + j) * 2];
}

static int twod(uint32_t i, uint32_t j)
{
    static int g[4][6];
    g[i * 2 + 1][j * 3 + 1] = 7;
    g[i + 1][j + 2] += 5;
    return g[1][2] + g[3][4] * 10 + g[2][3];
}

static long wide(long a)
{
    long s = 0;
    s += m[a * 2 + 1];
    s += m[(a + 1) * 3];
    return s;
}

int main(void)
{
    init();
    printf("%d\n", matrix(5));
    printf("%d %d\n", shifts(3, 2), shifts(4, 5));
    printf("%d %d\n", signedidx(2, 3), signedidx(1, 5));
    printf("%d %d\n", negconst(4), negconst(2));
    printf("%d %d\n", bitops(6), bitops(13));
    printf("%d %d\n", highbits(65536UL * 3 + 14), highbits(65537UL * 2));
    printf("%d %d\n", fromchars(3, 4), fromchars(2, 5));
    printf("%d\n", sidefx());
    printf("%d %d\n", ptrs(1), ptrs(2));
    printf("%d %d\n", structs(1, 1), structs(0, 2));
    printf("%d\n", twod(1, 1));
    printf("%d\n", (int)wide(4));
    return 0;
}
"""

# an index that wraps: the address is base + (index mod 65536) * 2
WRAP = r"""
#include <stdio.h>
#include <stdint.h>
int m[16];
static int wrap(uint32_t i)
{
    /* 65536 * 3 + i: the high part is gone from a 16-bit address */
    return m[i * 65537UL + 2];
}
static int wrapneg(int32_t i)
{
    return m[(i - 100000L) * 65536L + i];
}
int main(void)
{
    int i;
    for (i = 0; i < 16; i++) m[i] = 100 + i;
    printf("%d %d\n", wrap(3), wrap(1));
    printf("%d %d\n", wrapneg(4), wrapneg(7));
    return 0;
}
"""
# wrap(3): index 3*65537+2 = 196613 = 3*65536 + 5, low 16 bits 5 -> m[5] = 105
# wrap(1): 65539 -> 3 -> m[3] = 103; wrapneg(4): low 16 bits 4 -> 104
WRAP_EXPECT = "105 103\n104 107\n"


def host_expect(tmp_path, src):
    exe = tmp_path / "host"
    c_file = tmp_path / "host.c"
    c_file.write_text(src)
    r = subprocess.run(["cc", "-w", "-o", str(exe), str(c_file)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        pytest.skip("no host compiler")
    return subprocess.run([str(exe)], capture_output=True,
                          text=True, timeout=120).stdout


def narrowed(tmp_path, source, *flags):
    c_file = tmp_path / "d.c"
    c_file.write_text(source)
    env = dict(os.environ, UC80_NARROW_DEBUG="1")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), *flags, "-o",
         str(tmp_path / "d.mac")],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    return [ln[8:].split(":")[0] for ln in r.stderr.splitlines()
            if ln.startswith("narrow: ") and "narrowed" in ln]


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_programs_match_host(tmp_path, flags):
    want = host_expect(tmp_path, SRC)
    _, mac = compile_mac(tmp_path, SRC, *flags)
    assert run_mac(tmp_path, mac) == want


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_wrapping_index(tmp_path, flags):
    _, mac = compile_mac(tmp_path, WRAP, *flags)
    assert run_mac(tmp_path, mac) == WRAP_EXPECT


def test_pass_narrows_where_it_is_safe(tmp_path):
    fns = set(narrowed(tmp_path, SRC, "--no-inlining"))
    for name in ("matrix", "shifts", "signedidx", "negconst", "bitops",
                 "highbits", "fromchars", "sidefx", "ptrs", "structs",
                 "twod"):
        assert name in fns, name


def test_pass_stays_out_of_wide_and_off(tmp_path):
    # a 64-bit index (long long) and the switch
    src = """
int m[16];
int f(long long i) { return m[i * 2 + 1]; }
int g(int i) { return m[i * 2 + 1]; }
int h(unsigned char i) { return m[i]; }
int main(void) { return f(1) + g(1) + h(1); }
"""
    assert narrowed(tmp_path, src) == []
    assert narrowed(tmp_path, SRC, "--no-narrow-index") == []


def test_pointer_difference_is_not_narrowed(tmp_path):
    src = """
int m[16];
long f(int *p, int *q) { return (p - q) * 3L; }
int main(void) { return (int)f(&m[4], &m[1]); }
"""
    assert narrowed(tmp_path, src) == []


def test_multiply_helper_is_gone(tmp_path):
    src = """
unsigned long m[16];
unsigned long f(unsigned long i, unsigned long n, unsigned long j)
{ return m[i * n + j]; }
int main(void) { return (int)f(1, 2, 3); }
"""
    on, _ = compile_mac(tmp_path, src, "--no-inlining")
    off, _ = compile_mac(tmp_path, src, "--no-inlining",
                         "--no-narrow-index")
    assert "call\t__mul32" not in on
    assert "call\t__mul32" in off
