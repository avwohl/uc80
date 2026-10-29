"""Counters that are never negative (docs/range_compare.md).

The programs run in every mode and must print what the host compiler
prints.  The shape tests check with UC80_RANGE_DEBUG=1 which local
variables the pass puts in the set, and that the unsigned compare is
shorter than the signed one.
"""

import os
import subprocess
import sys

import pytest

from .test_register_cc import compile_mac, needs_run, run_mac
from .test_struct_return_abi import REPO

MODES = [
    (), ("--no-range-cmp",), ("-O0",), ("--no-register-locals",),
    ("--no-shared-storage",), ("--no-licm",), ("--no-inlining",),
    ("--no-register-cc",),
]

SRC = r"""
#include <stdio.h>
#include <stdint.h>

int g;
unsigned char arr[64];
static int getm(void) { return -3; }

/* the plain counter */
static int plain(void)
{
    int x, s = 0;
    for (x = 0; x < 80; x++)
        if (x > 40 && x <= 60)
            s += x;
    return s;
}

/* the boundary: the largest bound the pass accepts */
static int top1(void)
{
    int x, n = 0;
    for (x = 0; x < 32767; x++)
        n += (x > 32760);
    return n * 10 + (x == 32767);
}

static int top2(void)
{
    int x, n = 0;
    for (x = 0; x <= 32766; x++)
        n += (x > 32760);
    return n * 10 + (x > 32766);
}

static int step2(void)
{
    int x, n = 0;
    for (x = 0; x < 32766; x += 2)
        n += (x > 32760);
    return n * 10 + (x == 32766);
}

/* a negative constant is stored: the variable stays signed */
static int negconst(void)
{
    int x = 5, s = 0;
    for (; x < 8; x++)
        s++;
    x = -1;
    if (x < 3) s += 100;
    if (x > -5) s += 10;
    return s;
}

static int fromcall(void)
{
    int x = 0, s = 0;
    x = getm();
    if (x < 5) s += 1;
    for (; x < 5; x++) s += 10;
    return s;
}

static int addrtaken(void)
{
    int x, s = 0;
    int *p = &x;
    for (x = 0; x < 6; x++) {
        if (x < 3) s++;
        *p = x - 1;
        *p += 2;
    }
    return s;
}

static int bodystore(void)
{
    int x, s = 0;
    for (x = 0; x < 10; x++) {
        if (x == 3) x = 6;
        if (x < 7) s += x;
    }
    return s;
}

static int decr(void)
{
    int x, s = 0;
    for (x = 0; x < 10; x++) {
        s += x;
        x -= 2;
        x += 3;
        if (x > 4) break;
    }
    return s * 10 + (x < 0);
}

static int mixed(unsigned u)
{
    int x, s = 0;
    for (x = 0; x < 6; x++) {
        if (x < u) s++;
        if (u > x) s += 10;
    }
    return s;
}

static int negcmp(void)
{
    int x, s = 0;
    for (x = 0; x < 6; x++) {
        if (x > -1) s++;
        if (x < -1) s += 10;
        if (x >= 0) s += 100;
    }
    return s;
}

static int param(int x)
{
    int s = 0;
    for (; x < 6; x++)
        if (x < 2) s++;
    return s;
}

static int chars(void)
{
    signed char c;
    int s = 0;
    for (c = 0; c < 100; c++)
        if (c > 90) s++;
    return s;
}

static int twovars(void)
{
    int i, j, s = 0;
    for (i = 0; i < 10; i++)
        for (j = 0; j < 10; j++)
            if (j < i) s++;
    return s;
}

static int andcond(void)
{
    int x, s = 0;
    for (x = 0; x < 64 && arr[x] != 0; x++)
        s += arr[x];
    return s * 100 + x;
}

static int shadow(void)
{
    int x = 2, s = 0;
    {
        int x = -4;
        if (x < 0) s += 1;
    }
    for (; x < 5; x++)
        if (x > 1) s += 10;
    return s;
}

static int resets(void)
{
    int x, y = 0, s = 0;
    for (x = 0; x < 5; x++) {
        y = 7;
        if (y > x) s++;
        y = 20;
        if (y > x) s++;
    }
    return s;
}

static int jumped(int n)
{
    int x = 0, s = 0;
    if (n) goto in;
    for (x = 0; x < 5; x++) {
        s++;
    in:
        if (x < 3) s += 10;
    }
    return s;
}

int main(void)
{
    int i;
    for (i = 0; i < 10; i++) arr[i] = i + 1;
    printf("%d\n", plain());
    printf("%d %d %d\n", top1(), top2(), step2());
    printf("%d %d %d\n", negconst(), fromcall(), addrtaken());
    printf("%d %d %d\n", bodystore(), decr(), mixed(3));
    printf("%d %d %d\n", negcmp(), param(-3), chars());
    printf("%d %d %d\n", twovars(), andcond(), shadow());
    printf("%d %d %d\n", resets(), jumped(0), jumped(1));
    return 0;
}
"""


def host_expect(tmp_path):
    exe = tmp_path / "host"
    c_file = tmp_path / "host.c"
    c_file.write_text(SRC)
    r = subprocess.run(["cc", "-w", "-o", str(exe), str(c_file)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("no host compiler")
    return subprocess.run([str(exe)], capture_output=True,
                          text=True).stdout


def facts(tmp_path, source, *flags):
    c_file = tmp_path / "d.c"
    c_file.write_text(source)
    env = dict(os.environ, UC80_RANGE_DEBUG="1")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), *flags, "-o",
         str(tmp_path / "d.mac")],
        capture_output=True, text=True, cwd=str(REPO), env=env)
    assert r.returncode == 0, r.stderr
    out = {}
    for ln in r.stderr.splitlines():
        if ln.startswith("range: "):
            name, _, rest = ln[7:].partition(": ")
            out[name] = eval(rest)
    return out


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_programs_match_host(tmp_path, flags):
    want = host_expect(tmp_path)
    _, mac = compile_mac(tmp_path, SRC, *flags)
    assert run_mac(tmp_path, mac) == want


def test_counters_are_in_the_set(tmp_path):
    f = facts(tmp_path, SRC)
    for name, var in (("plain", "x"), ("top1", "x"), ("top2", "x"),
                      ("step2", "x"), ("mixed", "x"), ("negcmp", "x"),
                      ("twovars", "i"), ("twovars", "j"), ("andcond", "x"),
                      ("resets", "y"), ("resets", "x")):
        assert var in f[name], (name, var)


def test_other_variables_are_not(tmp_path):
    f = facts(tmp_path, SRC)
    for name in ("negconst", "fromcall", "addrtaken", "bodystore", "decr",
                 "param", "chars", "shadow", "jumped"):
        assert not f[name] or f[name] == ["y"] or f[name] == [], name
    for name, var in (("negconst", "x"), ("fromcall", "x"),
                      ("addrtaken", "x"), ("bodystore", "x"),
                      ("decr", "x"), ("param", "x"), ("chars", "c"),
                      ("shadow", "x"), ("jumped", "x")):
        assert var not in f[name], (name, var)


def test_bound_just_over_the_limit_stays_signed(tmp_path):
    src = """
int f(void) { int x, n = 0; for (x = 0; x <= 32767; x++) { n++; if (n > 5) break; } return n; }
int g(void) { int x, n = 0; for (x = 0; x < 32766; x += 3) { n++; if (n > 5) break; } return n; }
int main(void) { return f() + g(); }
"""
    f = facts(tmp_path, src)
    assert "x" not in f["f"]
    assert "x" not in f["g"]


def test_switch_turns_the_pass_off(tmp_path):
    f = facts(tmp_path, SRC, "--no-range-cmp")
    assert not f


def test_unsigned_compare_is_shorter(tmp_path):
    src = """
unsigned char vi[1920];
int f(void) { int y, x, s = 0;
  for (y = 0; y < 24; y++) for (x = 0; x < 80; x++) s += vi[y * 80 + x];
  return s; }
int main(void) { return f(); }
"""
    on, _ = compile_mac(tmp_path, src, "--no-inlining")
    off, _ = compile_mac(tmp_path, src, "--no-inlining", "--no-range-cmp")
    assert len(on.splitlines()) < len(off.splitlines())
