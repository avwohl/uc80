"""Repairs found after the integrate10 merge.  Every program runs in
cpmemu and the output is compared with the host C compiler."""

import shutil
import subprocess

import pytest

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401

HOST_CC = shutil.which("cc")
CONFIGS = [
    pytest.param((), id="default"),
    pytest.param(("--int", "32"), id="int32"),
    pytest.param(("--no-licm", "--no-range-cmp", "--no-narrow-index"),
                 id="loops-off"),
]


def host(tmp_path, src):
    c = tmp_path / "h.c"
    c.write_text(src)
    exe = tmp_path / "h"
    subprocess.run([HOST_CC, "-w", "-o", str(exe), str(c)], check=True,
                   capture_output=True, text=True, timeout=120)
    return subprocess.run([str(exe)], capture_output=True, text=True,
                          timeout=60).stdout


ENUM_DEP = r'''#include <stdio.h>
enum { GA = 2, GB = GA << 3, GC = GB | 1 };
enum { U = 7, V = U, W = V + U };
unsigned char tab[40];
int main(void)
{
    enum { P = 5, Q = P * 2, R = Q - P - 12 };
    enum { E0 = 3, E1 = E0 * 5 + 1 };
    long lq = 0x7777; int i, s = 0;
    printf("%d %d %d\n", GA, GB, GC);
    printf("%d %d %d\n", U, V, W);
    printf("%d %d %d\n", P, Q, R);
    for (i = 0; i < 40; i++) tab[i] = (unsigned char)(i * E1);
    for (i = 0; i < 40; i++) s += tab[i] + (int)(lq & E1) + (GC & i);
    printf("%d %d\n", s, E1);
    return 0;
}
'''

PTR_SHADOW = r'''#include <stdio.h>
static void die(short v) { printf("global die %d\n", v); }
static void soft(short v) { printf("soft %d\n", v); }
static void viaptr(void (*die)(short), short v) { die(v); }
int main(void)
{
    void (*cb)(short) = soft;
    void (*die2)(short) = soft;
    viaptr(soft, 5);
    cb(6);
    die2(7);
    die(8);
    return 0;
}
'''

PUTC = r'''#include <stdio.h>
int main(void)
{
    int r = fputc('R', stdout);
    int q = putc('Q', stdout);
    putchar('\n');
    printf("%d %d\n", r, q);
    return 0;
}
'''


@pytest.mark.parametrize("flags", CONFIGS)
def test_enumerator_initializer_names_earlier_enumerator(tmp_path, flags):
    got = build_and_run(tmp_path, ENUM_DEP, flags=flags)
    assert got.replace("\r", "") == host(tmp_path, ENUM_DEP)


@pytest.mark.parametrize("flags", CONFIGS)
def test_call_through_pointer_named_like_a_function(tmp_path, flags):
    got = build_and_run(tmp_path, PTR_SHADOW, flags=flags)
    assert got.replace("\r", "") == host(tmp_path, PTR_SHADOW)


@pytest.mark.parametrize("flags", CONFIGS)
def test_fputc_putc_to_stdout(tmp_path, flags):
    got = build_and_run(tmp_path, PUTC, flags=flags)
    assert got.replace("\r", "") == host(tmp_path, PUTC)


INT32_ARITH = r'''#include <stdio.h>
int main(void)
{
    volatile unsigned short us = 60000, u2 = 50000;
    volatile short s = 30000, s2 = -20000;
    short a = 3, b = 1, c = 4;
    printf("%ld\n", (long)(us + u2));
    printf("%ld\n", (long)(s + s));
    printf("%ld\n", (long)(s2 - s));
    printf("%ld\n", (long)(us << 4));
    printf("%ld\n", (long)~us);
    printf("%ld\n", (long)-us);
    printf("%ld\n", (long)-s2);
    printf("%ld\n", (long)(us + us));
    printf("%d\n", us + u2 > 0);
    printf("%ld\n", (long)((us + u2) / 3));
    printf("%d\n", (a * b) * (a + 3));
    printf("%d\n", (a * b) * (c * a));
    printf("%d\n", (a * b) * (a < b));
    printf("%d\n", (a * b) * (-c));
    printf("%d\n", (a * b) * (short)(c + 1));
    printf("%d\n", (a * b) * (c + a) * (a + b));
    return 0;
}
'''


def test_int32_short_arithmetic_and_tmp32(tmp_path):
    got = build_and_run(tmp_path, INT32_ARITH, flags=("--int", "32"))
    exp = host(tmp_path, INT32_ARITH)
    assert got.replace("\r", "") == exp


NR_SHADOW = r'''#include <stdio.h>
#include <stdlib.h>
[[noreturn]] static void die(short v) { printf("die %d\n", v); exit(3); }
static void soft(short v) { printf("soft %d\n", v); }
static int viaptr(void (*die)(short), short v)
{
    die(v);
    printf("after %d\n", v);
    return v + 1;
}
int main(void)
{
    int r = viaptr(soft, 5);
    printf("r %d\n", r);
    die(9);
    return 0;
}
'''


@pytest.mark.parametrize("flags", CONFIGS)
def test_noreturn_function_hidden_by_a_parameter(tmp_path, flags):
    got = build_and_run(tmp_path, NR_SHADOW, flags=flags)
    assert got.replace("\r", "") == host(tmp_path, NR_SHADOW)
