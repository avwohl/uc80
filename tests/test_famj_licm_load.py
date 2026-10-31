"""Family J step 3: loop-invariant loads and row addresses (docs/licm_load.md).

Each program runs under cpmemu (with a timeout) and on the host compiler.
Both outputs must be the same.  The shape tests read UC80_LICM_DEBUG=1
output: the pass moves a load or a row address where this is safe and
stays out where a store, a call, a volatile object or a jump could
change the answer.
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
    (), ("--no-licm-load",), ("--no-licm",), ("-O0",),
    ("--no-register-locals",), ("--no-addr-cse",), ("--int=32",),
    ("--no-register-cc",), ("--isr-safe",),
]

SRC = r"""
#include <stdio.h>

int lim[8] = {3, 5, 2, 7, 4, 1, 6, 0};
int grid[6][9];
unsigned char bytes[6][8];
int col[40];
int shared[8] = {1, 2, 3, 4, 5, 6, 7, 8};
volatile int vlim[4] = {2, 3, 4, 5};
int total;
int *pp;

static void setup(void)
{
    int i, j;
    for (i = 0; i < 6; i++)
        for (j = 0; j < 9; j++)
            grid[i][j] = (i * 9 + j) % 4;
    for (i = 0; i < 6; i++)
        for (j = 0; j < 8; j++)
            bytes[i][j] = (unsigned char)(i * 8 + j);
    for (i = 0; i < 40; i++)
        col[i] = i & 3;
}

/* the shape of Fit: bound and row move, the column stays */
static int fit(int i, int j)
{
    int k;
    for (k = 0; k <= lim[i]; k++)
        if (grid[i][k])
            if (col[j + k])
                return 0;
    return 1;
}

static int place(int i, int j)
{
    int k;
    for (k = 0; k <= lim[i]; k++)
        if (grid[i][k])
            col[j + k] = 1;
    return col[j];
}

/* the bound array is written in the loop: nothing moves */
static int bound_written(int i)
{
    int k, n = 0;
    for (k = 0; k <= lim[i]; k++) {
        n++;
        if (k == 1)
            lim[i] = 2;
    }
    return n;
}

/* a store through a pointer may change the bound */
static int bound_by_pointer(int i)
{
    int k, n = 0;
    pp = &lim[i];
    for (k = 0; k <= lim[i]; k++) {
        n++;
        if (k == 1)
            *pp = 2;
    }
    lim[i] = 6;
    return n;
}

static void touch(int i) { lim[i] = 1; }

/* a call may change the bound */
static int bound_by_call(int i)
{
    int k, n = 0;
    for (k = 0; k <= lim[i]; k++) {
        n++;
        if (k == 2)
            touch(i);
    }
    lim[i] = 5;
    return n;
}

/* the loop may run zero times */
static int zero_trip(int i, int n)
{
    int k, s = 0;
    for (k = 0; k < n; k++)
        s += shared[i] + grid[i][k];
    return s;
}

/* a volatile bound is read in every pass */
static int volatile_bound(int i)
{
    int k, n = 0;
    for (k = 0; k < vlim[i]; k++) {
        n++;
        if (n > 20)
            break;
    }
    return n;
}

/* a store to another array is no reason to stay */
static int other_store(int i)
{
    int k, s = 0;
    for (k = 0; k < 6; k++) {
        col[k] = k + lim[i];
        s += shared[i];
    }
    return s;
}

/* a store to a row of the same array: the row address is the same,
   the loaded values are not */
static int same_array_store(int i)
{
    int k, s = 0;
    for (k = 0; k < 5; k++) {
        grid[i][k] = grid[i][k] + 1;
        s += grid[i][k] + grid[i + 1][k];
    }
    return s;
}

/* the index changes in the loop */
static int index_changes(void)
{
    int i = 0, s = 0;
    while (i < 5) {
        s += lim[i] * 3 + grid[i][1];
        i++;
    }
    return s;
}

/* a 2 dimensional byte array and a 32-bit index sum */
static long bytesum(int r)
{
    int c;
    long s = 0;
    for (c = 0; c < 8; c++)
        s += bytes[r][c] * (long)(c + 1);
    return s;
}

/* a break before the use: the load must not happen too early */
static int guarded(int i, int t)
{
    int k, s = 0;
    for (k = 0; k < 4; k++) {
        if (k == t)
            break;
        s += lim[i];
    }
    return s;
}

/* a label in the loop */
static int with_label(int i)
{
    int k, s = 0;
    for (k = 0; k < 4; k++) {
        s += lim[i];
        if (k == 1)
            goto skip;
        s += 1;
skip:
        s += grid[i][k];
    }
    return s;
}

/* nested loops: the inner bound moves out of the inner loop only */
static int nested(void)
{
    int i, k, s = 0;
    for (i = 0; i < 6; i++)
        for (k = 0; k < lim[i]; k++)
            s += grid[i][k] + bytes[i][k];
    return s;
}

int main(void)
{
    int i, s = 0;
    setup();
    for (i = 0; i < 6; i++) {
        s += fit(i, i + 2);
        s += place(i, i * 3);
    }
    printf("%d %d\n", s, col[7]);
    s = bound_written(1);
    printf("%d %d\n", s, lim[1]);
    lim[1] = 5;
    s = bound_by_pointer(2);
    printf("%d %d\n", s, lim[2]);
    s = bound_by_call(3);
    printf("%d %d\n", s, lim[3]);
    s = zero_trip(2, 0);
    printf("%d %d\n", s, zero_trip(2, 4));
    printf("%d\n", volatile_bound(1));
    printf("%d\n", other_store(4));
    printf("%d\n", same_array_store(2));
    printf("%d\n", index_changes());
    { long a = bytesum(0); printf("%ld %ld\n", a, bytesum(5)); }
    s = guarded(3, 0);
    printf("%d %d\n", s, guarded(3, 9));
    printf("%d\n", with_label(2));
    printf("%d\n", nested());
    return 0;
}
"""


@pytest.mark.parametrize("flags", FLAGS, ids=ids)
def test_licm_load_against_host(tmp_path, flags):
    same_as_host(tmp_path, SRC, *flags)


def debug_lines(tmp_path, src, *flags):
    c_file = tmp_path / "d.c"
    c_file.write_text(src)
    env = dict(os.environ, UC80_LICM_DEBUG="1")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), *flags,
         "-o", str(tmp_path / "d.mac")],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=300)
    assert r.returncode == 0, r.stderr
    return [ln for ln in r.stderr.splitlines() if ln.startswith("licm:")]


def moved(lines, fn):
    return [ln for ln in lines if ln.startswith(f"licm: {fn}:")]


def test_licm_load_moves_bound_and_row(tmp_path):
    lines = debug_lines(tmp_path, SRC)
    fit = moved(lines, "fit")
    assert any("M:G:lim" in ln for ln in fit)
    assert any("R:G:grid" in ln for ln in fit)
    assert any("M:G:lim" in ln for ln in moved(lines, "place"))
    assert any("M:G:shared" in ln for ln in moved(lines, "zero_trip"))
    assert any("M:G:lim" in ln for ln in moved(lines, "other_store"))
    assert any("M:G:lim" in ln for ln in moved(lines, "nested"))


@pytest.mark.parametrize("fn", [
    "bound_written", "bound_by_pointer", "bound_by_call", "volatile_bound",
    "index_changes", "guarded", "with_label",
])
def test_licm_load_stays_out(tmp_path, fn):
    lines = debug_lines(tmp_path, SRC)
    assert not any("M:" in ln for ln in moved(lines, fn)), moved(lines, fn)


def test_licm_load_row_only_when_array_stored(tmp_path):
    lines = moved(debug_lines(tmp_path, SRC), "same_array_store")
    assert not any("M:" in ln for ln in lines)
    assert any("R:G:grid" in ln for ln in lines)


def test_licm_load_switch_off(tmp_path):
    assert debug_lines(tmp_path, SRC, "--no-licm-load")
    assert not any(("M:" in ln or "R:" in ln or "A:" in ln) for ln in
                   debug_lines(tmp_path, SRC, "--no-licm-load"))
    assert not debug_lines(tmp_path, SRC, "--no-licm")


ISR_SRC = r"""
#include <stdio.h>
unsigned char tab[8] = {1, 2, 3, 4, 5, 6, 7, 8};
int cnt[4];

#pragma uc80 interrupt isr
void isr(void)
{
    cnt[0]++;
}

int scan(int i, int n)
{
    int k, s = 0;
    for (k = 0; k < n; k++)
        s += cnt[i] + tab[i];
    return s;
}

int main(void)
{
    printf("%d\n", scan(1, 3));
    return 0;
}
"""


def test_licm_load_array_of_an_interrupt_function(tmp_path):
    c_file = tmp_path / "i.c"
    c_file.write_text(ISR_SRC)
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), "--isr-safe",
         "-o", str(tmp_path / "i.mac")],
        capture_output=True, text=True, cwd=str(REPO),
        env=dict(os.environ, UC80_LICM_DEBUG="1"), timeout=300)
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in r.stderr.splitlines() if ln.startswith("licm:")]
    assert not any("G:cnt" in ln for ln in lines)
    assert any("G:tab" in ln for ln in lines)
