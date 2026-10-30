"""Loop-invariant code motion (docs/licm.md).

The programs run in every mode and must print what the host compiler
prints (values are clean for a 16-bit int).  The shape tests check with
UC80_LICM_DEBUG=1 that the pass moves an expression where this is safe
and stays out where an assignment, an address, a volatile object, a
global, a division or a jump into the loop could change the answer.
"""

import os
import subprocess
import sys

import pytest

from .test_register_cc import compile_mac, needs_run, run_mac
from .test_struct_return_abi import REPO

MODES = [
    (), ("--no-licm",), ("-O0",), ("--no-register-locals",),
    ("--no-shared-storage",), ("--no-addr-cse",), ("--no-inlining",),
    ("--no-register-cc",), ("--isr-safe",),
]

SRC = r"""
#include <stdio.h>
#include <stdint.h>

unsigned char vi[6 * 10], ph[6 * 10];
int gm = 3;
volatile int vm = 2;

static void init(void)
{
    int i;
    for (i = 0; i < 60; i++) { vi[i] = (i * 7) & 31; ph[i] = (i * 5) & 31; }
}

/* the shape of tc_wr: y * 10 does not change in the inner loop */
static int tcwr(void)
{
    int y, x, i, c, s = 0;
    for (y = 0; y < 6; y++) {
        for (x = 0; x < 10; x++) {
            i = y * 10 + x;
            c = vi[i];
            if (c == ph[i])
                continue;
            s += c + y;
        }
    }
    return s;
}

/* y changes inside the inner loop: nothing moves */
static int ychanges(void)
{
    int y = 1, x, s = 0;
    for (x = 0; x < 6; x++) {
        s += y * 10 + x;
        y++;
    }
    return s;
}

/* y is assigned in the update clause of the inner loop */
static int yupdate(void)
{
    int y = 1, x, s = 0;
    for (x = 0; x < 6; x++, y += 2)
        s += y * 12 + x;
    return s;
}

static int addr_taken(void)
{
    int y = 2, x, s = 0;
    int *p = &y;
    for (x = 0; x < 5; x++) {
        s += y * 12 + x;
        *p += 1;
    }
    return s;
}

static int vol_local(void)
{
    volatile int y = 2;
    int x, s = 0;
    for (x = 0; x < 5; x++)
        s += y * 12 + x;
    return s;
}

static int globals(void)
{
    int x, s = 0;
    for (x = 0; x < 5; x++) {
        s += gm * 12 + x;
        gm++;
    }
    return s + vm * 12;
}

static int divides(int d)
{
    int x, s = 0;
    for (x = 0; x < 5; x++)
        s += (d / 3) * 12 + x;
    return s;
}

static int zero_trip(int n)
{
    int y = 5, x, s = 100;
    for (x = 0; x < n; x++)
        s += y * 12 + x;
    while (x < 0)
        s += y * 12;
    return s;
}

static int jumps(int n)
{
    int y = 3, x, s = 0;
    for (x = 0; x < 20; x++) {
        s += y * 12 - 30;
        if (x == n)
            break;
        if (x & 1)
            continue;
        s += y * 12 + x;
        if (s > 400)
            goto out;
    }
    s += 1000;
out:
    return s;
}

/* the case labels belong to a switch outside the loop (Duff's device) */
static int duff(int n)
{
    int y = 3, s = 0;
    switch (n & 3) {
    case 0: do { s += y * 12;
    case 3:      s += y * 12 + 1;
    case 2:      s += y * 12 + 2;
    case 1:      s += y * 12 + 3;
            } while ((n -= 4) > 0);
    }
    return s;
}

/* a switch inside the loop */
static int inner_switch(void)
{
    int y = 4, x, s = 0;
    for (x = 0; x < 8; x++) {
        s += y * 12 - x;
        switch (x & 3) {
        case 0: s += y * 12; break;
        case 1: s += y * 12 + 1; break;
        default: s += x; break;
        }
    }
    return s;
}

/* the product is evaluated in some passes only: it stays in place */
static int rare(int n)
{
    int y = n, x, s = 0;
    for (x = 0; x < 9; x++) {
        if (x == 7)
            s += y * 40;
        s += (x > 100 && y * 20 > 5) ? y * 24 : 1;
        switch (x) {
        case 3: s += y * 36; break;
        default: break;
        }
    }
    return s;
}

/* a macro like do { ... } while (0) runs once: nothing gains */
#define STEP(a, b) do { s += (a) * 40 + (b) * 24; } while (0)
static int once(int n)
{
    int y = n, z = n + 1, s = 0;
    STEP(y, z);
    STEP(z, y);
    return s;
}

static int rec(int n, int y)
{
    int x, s = 0;
    if (n == 0)
        return 0;
    for (x = 0; x < 3; x++)
        s += y * 12 + x + rec(n - 1, y + 1);
    return s;
}

static int three_deep(void)
{
    int a, b, c, s = 0, k = 7;
    for (a = 0; a < 3; a++)
        for (b = 0; b < 3; b++)
            for (c = 0; c < 3; c++)
                s += k * 20 + a * 11 + b * 13 + c;
    return s;
}

static int shadow(void)
{
    int y = 2, x, s = 0;
    for (x = 0; x < 4; x++) {
        int t = y * 12;
        {
            int y = x + 1;
            t += y * 12;
        }
        s += t;
    }
    return s;
}

static int whiles(int n)
{
    int y = n, k = 0, s = 0;
    while (k < 5) {
        s += y * 12 + k;
        k++;
    }
    do {
        s += y * 24 - k;
        k--;
    } while (k > 2);
    return s;
}

static long wide(long a, int n)
{
    long s = 0;
    int i;
    for (i = 0; i < n; i++)
        s += a * 1000L + i;
    return s;
}

static int small_types(unsigned char u, signed char c)
{
    int x, s = 0;
    for (x = 0; x < 4; x++) {
        s += u * c + x;
        s += (u << 5) + (c << 3);
        s += (unsigned char)(u * 3 + 200) + (int)(c * 12);
        s += ((u ^ 0x55) * 10) & 0xff;
    }
    return s;
}

static int shifts(int a, int b)
{
    int x, s = 0;
    for (x = 0; x < 4; x++) {
        s += (a << 4) + (a >> 2) * 3 + (b << 9);
        s += (a << 4) - x;
    }
    return s;
}

static int negs(int a)
{
    int x, s = 0;
    for (x = 0; x < 4; x++)
        s += -a * 10 + (~a) * 6 + x;
    return s;
}

static int two_equal(int y)
{
    int x, s = 0;
    for (x = 0; x < 4; x++) {
        s += y * 25 + x;
        s -= y * 25 - x;
        s += (y * 25) / 5;
    }
    return s;
}

/* 32-bit index arithmetic: only low 16 bits reach the address */
static int idx32(uint32_t n)
{
    static int m[16];
    uint32_t i, j;
    int s = 0;
    for (i = 0; i < 4; i++) {
        for (j = 0; j < 4; j++)
            m[i * 4 + j] = (int)(i * n + j);
    }
    for (i = 0; i < 4; i++)
        for (j = 0; j < 4; j++)
            s += m[i * 4 + j];
    return s;
}

int main(void)
{
    init();
    printf("%d\n", tcwr());
    printf("%d %d %d %d\n", ychanges(), yupdate(), addr_taken(), vol_local());
    printf("%d %d\n", globals(), divides(31));
    printf("%d %d %d\n", zero_trip(0), zero_trip(3), jumps(7));
    printf("%d %d %d\n", jumps(30), duff(6), inner_switch());
    printf("%d %d %d\n", rec(3, 1), three_deep(), shadow());
    printf("%d %d %d\n", rare(3), rare(5), once(4));
    printf("%d %d\n", whiles(3), (int)(wide(123456L, 3) % 10007));
    printf("%d %d\n", small_types(200, -3), shifts(37, 3));
    printf("%d %d %d\n", negs(9), two_equal(7), idx32(5));
    return 0;
}
"""

# what the host compiler prints for SRC (values clean for 16-bit int)
EXPECT = None


def host_expect(tmp_path):
    exe = tmp_path / "host"
    c_file = tmp_path / "host.c"
    c_file.write_text(SRC)
    r = subprocess.run(["cc", "-w", "-o", str(exe), str(c_file)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        pytest.skip("no host compiler")
    return subprocess.run([str(exe)], capture_output=True,
                          text=True, timeout=120).stdout


def licm_log(tmp_path, source, *flags):
    """Names of the functions in which the pass moved an expression."""
    c_file = tmp_path / "d.c"
    c_file.write_text(source)
    env = dict(os.environ, UC80_LICM_DEBUG="1")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), *flags, "-o",
         str(tmp_path / "d.mac")],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    return [ln[6:] for ln in r.stderr.splitlines() if ln.startswith("licm: ")]


def moved(log):
    return {ln.split(":")[0] for ln in log if "uses=" in ln}


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_programs_match_host(tmp_path, flags):
    want = host_expect(tmp_path)
    _, mac = compile_mac(tmp_path, SRC, *flags)
    assert run_mac(tmp_path, mac) == want


def test_pass_moves_where_it_is_safe(tmp_path):
    fns = moved(licm_log(tmp_path, SRC))
    for name in ("tcwr", "zero_trip", "jumps", "inner_switch", "rec",
                 "three_deep", "whiles", "wide", "small_types", "shifts",
                 "negs", "two_equal", "idx32"):
        assert name in fns, name


def test_pass_stays_out_where_a_value_can_change(tmp_path):
    fns = moved(licm_log(tmp_path, SRC))
    # ychanges, yupdate: y is assigned in the loop; addr_taken: &y;
    # vol_local: volatile; globals: a global; divides: a division (a
    # division can trap and costs the same in every pass);
    # duff: case labels of a switch outside the loop; rare: each product
    # is evaluated in some passes only (an if, a && or ?: branch, a case)
    for name in ("ychanges", "yupdate", "addr_taken", "vol_local",
                 "globals", "divides", "duff", "rare", "once"):
        assert name not in fns, name


def test_one_temp_for_equal_expressions(tmp_path):
    log = licm_log(tmp_path, SRC)
    two = [ln for ln in log if ln.startswith("two_equal:")]
    assert len(two) == 1 and "uses=3" in two[0]


def test_nested_loops_move_to_the_inner_loop_only(tmp_path):
    src = """
int f(int n) {
    int y, x, s = 0;
    for (y = 0; y < n; y++)
        for (x = 0; x < 5; x++)
            s += y * 40 + x;
    return s;
}
int main(void) { return f(3); }
"""
    assert len(licm_log(tmp_path, src)) == 1


def test_invariant_of_both_loops_goes_to_the_outer_loop(tmp_path):
    src = """
int f(int k) {
    int y, x, s = 0;
    for (y = 0; y < 3; y++)
        for (x = 0; x < 5; x++)
            s += k * 40 + x;
    return s;
}
int main(void) { return f(3); }
"""
    log = licm_log(tmp_path, src)
    assert len(log) == 1
    assert "uses=1" in log[0]


def test_switch_turns_the_pass_off(tmp_path):
    assert moved(licm_log(tmp_path, SRC, "--no-licm")) == set()


def test_interrupt_function_is_left_alone(tmp_path):
    src = """
volatile int ticks;
int out;
#pragma uc80 interrupt isr
void isr(void) { int y = 3, x; for (x = 0; x < 4; x++) ticks += y * 40 + x; }
int main(void) { int y = 3, x; for (x = 0; x < 4; x++) out += y * 40 + x;
                 return 0; }
"""
    c_file = tmp_path / "i.c"
    c_file.write_text(src)
    env = dict(os.environ, UC80_LICM_DEBUG="1")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), "--target", "rom",
         "-o", str(tmp_path / "i.mac")],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    fns = {ln[6:].split(":")[0] for ln in r.stderr.splitlines()
           if ln.startswith("licm: ")}
    assert "isr" not in fns
    assert "main" in fns


def test_loop_with_a_label_is_left_alone(tmp_path):
    src = """
int f(int n) {
    int y = n, x = 0, s = 0;
    if (n == 7) goto inside;
    for (x = 0; x < 5; x++) {
        s += y * 40;
    inside:
        s += y * 40 + x;
    }
    return s;
}
int main(void) { return f(3); }
"""
    assert moved(licm_log(tmp_path, src)) == set()


@needs_run
def test_jump_into_a_loop_runs_right(tmp_path):
    src = """
#include <stdio.h>
int f(int n) {
    int y = n, x = 0, s = 0;
    if (n == 7) goto inside;
    for (x = 0; x < 3; x++) {
        s += y * 40;
    inside:
        s += y * 40 + x;
    }
    return s;
}
int main(void) { printf("%d %d\\n", f(3), f(7)); return 0; }
"""
    for flags in ((), ("--no-licm",)):
        _, mac = compile_mac(tmp_path, src, *flags)
        assert run_mac(tmp_path, mac) == "723 1403\n"
