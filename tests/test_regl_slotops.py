"""Register locals and the slot-operand fold (docs/register_locals.md).

The code generator reaches a byte local in shared storage with
``ld HL,??AUTO+k`` and an operation on ``(HL)`` (inc, dec, cp, add, xor,
bit, set, a shift).  The pass folds the pair into one access of the slot,
so the local can live in a register.  The programs run in cpmemu with the
fold on, off (--no-slot-fold), with --no-register-locals and with -O0.  The
outputs must be equal.
"""

import os
import re
import subprocess
import sys

import pytest

from .test_register_cc import (REPO, build_run, compile_mac, func,
                               needs_run)

VARIANTS = [(), ("--no-slot-fold",), ("--no-register-locals",), ("-O0",),
            ("--no-slot-fold", "--no-peep-pairs", "--no-discard-comma")]

BYTES = r"""
#include <stdio.h>
static unsigned char tab[8] = {3, 250, 7, 128, 77, 0, 255, 19};
static unsigned char sink[64];
static int ns;
static void put(unsigned char a, unsigned char b, unsigned char c)
{
    sink[ns & 63] = (unsigned char)(a + b * 3 + c * 5);
    ns++;
}
static int run(int n)
{
    unsigned char val, i, lim = 100, k = 0;
    int s = 0;
    for (i = 0; i < n; i++) {
        val = tab[i & 7];
        if (val & 1) val += i; else val ^= i;
        val |= 0x80;
        val &= 0xDF;
        val--;
        if (val == lim) k++;
        if (val < lim) k += 2;
        val <<= 1;
        val >>= 1;
        put(i, val, (unsigned char)(val + 1));
        s += val + k;
    }
    return s;
}
int main(void)
{
    int i, t = 0;
    printf("%d\n", run(60));
    for (i = 0; i < 64; i++) t = t * 31 + sink[i];
    printf("%d %d\n", t, ns);
    return 0;
}
"""

COUNTERS = r"""
#include <stdio.h>
static int calls;
static void tick(unsigned char x) { calls += x; }
static int f(unsigned char n)
{
    unsigned char a = 0, b = 255, c = 7;
    int r = 0;
    while (a != n) {
        a++;
        b--;
        c += a;
        c -= 3;
        c ^= b;
        if (c > 128) r++;
        if ((c & 3) == 2) r += 5;
        tick(c);
    }
    return r * 100 + a + (b & 15) + (c & 15);
}
int main(void)
{
    printf("%d %d\n", f(10), calls);
    printf("%d %d\n", f(200), calls);
    return 0;
}
"""

ADDRESS = r"""
#include <stdio.h>
static void bump(unsigned char *p) { (*p)++; }
static int f(void)
{
    unsigned char a = 5, b = 9;
    int i;
    for (i = 0; i < 10; i++) { a++; b ^= a; bump(&a); }
    return a * 256 + b;
}
static int g(void)
{
    volatile unsigned char v = 1;
    unsigned char w = 2;
    int i;
    for (i = 0; i < 10; i++) { v++; w += v; v ^= w; }
    return v * 256 + w;
}
int main(void)
{
    printf("%d %d\n", f(), g());
    return 0;
}
"""

PROGRAMS = {"bytes": BYTES, "counters": COUNTERS, "address": ADDRESS}


@needs_run
@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_every_option_set_gives_the_same_output(tmp_path, name):
    outs = []
    for i, flags in enumerate(VARIANTS):
        d = tmp_path / f"v{i}"
        d.mkdir()
        outs.append(build_run(d, PROGRAMS[name], "--no-inlining", *flags))
    assert outs[0].strip(), "no output"
    assert all(o == outs[0] for o in outs), list(zip(VARIANTS, outs))


def _debug_log(tmp_path, src, *flags):
    (tmp_path / "t.c").write_text(src)
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(tmp_path / "t.c"),
                        "--no-inlining", *flags, "-o", str(tmp_path / "t.mac")],
                       capture_output=True, text=True, cwd=str(REPO),
                       env=dict(os.environ, UC80_RL_DEBUG="1"))
    assert r.returncode == 0, r.stderr
    return r.stderr


def test_the_fold_removes_the_slot_address_rejection(tmp_path):
    on = _debug_log(tmp_path, BYTES)
    off = _debug_log(tmp_path, BYTES, "--no-slot-fold")
    assert "address of a slot" in off
    assert "address of a slot" not in on


def test_a_folded_local_is_in_a_register(tmp_path):
    on, _ = compile_mac(tmp_path, BYTES, "--no-inlining")
    off, _ = compile_mac(tmp_path, BYTES, "--no-inlining", "--no-slot-fold")
    assert (len(re.findall(r"\?\?AUTO", func(on, "run")))
            < len(re.findall(r"\?\?AUTO", func(off, "run"))))


def test_an_address_taken_or_volatile_local_stays_in_memory(tmp_path):
    mac, _ = compile_mac(tmp_path, ADDRESS, "--no-inlining")
    # f: a has its address taken; g: v is volatile.  Both stay in memory,
    # so the slot is still named in the function text.
    assert "??AUTO" in func(mac, "f")
    assert "??AUTO" in func(mac, "g")


ISR = r"""
volatile unsigned char n, m;
#pragma uc80 interrupt isr
void isr(void) {
    unsigned char a = n, b = 3;
    int i;
    for (i = 0; i < 8; i++) { a++; b ^= a; if (a == 7) b--; }
    m = b;
}
int main(void) { return 0; }
"""


def test_an_interrupt_function_keeps_its_code(tmp_path):
    texts = []
    for flags in ((), ("--no-slot-fold",)):
        d = tmp_path / ("x" + str(len(texts)))
        d.mkdir()
        mac, _ = compile_mac(d, ISR, "--target=rom", *flags)
        texts.append(func(mac, "isr"))
    assert texts[0] == texts[1]


NAKED = r"""
#include <stdio.h>
int clob(int a) __naked
{
    __asm
        ld hl,#2
        add hl,sp
        ld a,(hl)
        inc hl
        ld h,(hl)
        ld l,a
        ld bc,#0x1111
        ret
    __endasm;
}
static int f(void)
{
    unsigned char a = 1, b = 2;
    int i, s = 0;
    for (i = 0; i < 5; i++) { a++; b += a; s += clob(b); }
    return s + a;
}
int main(void) { printf("%d\n", f()); return 0; }
"""


@needs_run
def test_a_naked_callee_between_folded_accesses(tmp_path):
    outs = []
    for i, flags in enumerate(VARIANTS):
        d = tmp_path / f"n{i}"
        d.mkdir()
        outs.append(build_run(d, NAKED, "--no-inlining", *flags))
    assert all(o == outs[0] for o in outs), outs
