"""Dead-local elimination (docs/dead_store.md).

The pass removes the stores to a local that nothing reads.  The program
below runs in every mode and must print what the host compiler prints
(clean for a 16-bit int).  It has stores that are dead and stores that
only look dead: a call in the right side, a volatile access, an address
taken, a global of the same name, a read in a branch, a value that an
enclosing expression uses.  The shape tests check which names the pass
removes and which it leaves alone.
"""

import os
import subprocess
import sys

import pytest

from .test_register_cc import compile_mac, needs_run, run_mac
from .test_struct_return_abi import REPO

MODES = [
    (), ("--no-dead-store",), ("--no-addr-cse",), ("--no-register-locals",),
    ("--no-shared-storage",), ("-O0",), ("--no-inlining",),
    ("--no-register-cc",), ("--isr-safe",), ("--no-ast-optimize",),
]

SRC = r"""
#include <stdio.h>

unsigned char flags[100];
int gcount;
int gv;
volatile int vg;
int shared;
int bump(void) { gcount++; return gcount; }
int twice(int a) { gcount += a; return a * 2; }

static int sieve(void)
{
    unsigned i, k, count;
    count = 98;
    for (i = 2; i < 10; i++)
        if (!flags[i])
            for (k = i * i; k < 100; k += i) {
                count -= !flags[k];
                flags[k] = 1;
            }
    return flags[49] + flags[7] * 2;
}

static int effect_rhs(void)
{
    int r, q = 5, z = bump(), a = 1, b = twice(3), c = 2;
    r = bump();
    r = twice(r);
    q += twice(4);
    gcount += 100;
    return gcount;
}

static int chain(void)
{
    int a, b, c;
    a = b = c = bump();
    a = (b = twice(2));
    return gcount;
}

static int selfref(int n)
{
    int x = 0, y = 1, i;
    for (i = 0; i < n; i++) {
        x = x + 1;
        y += x;
        y = y * 2;
    }
    return i;
}

static int comma(int n)
{
    int i, j = 0, d = 0, e = 0;
    for (i = 0; i < n; i++, d++, j += 2, e++)
        ;
    d = j, j = d;
    return i + j;
}

static int postinc(void)
{
    int x = 3, y, w = 7;
    y = x++;
    y = w--;
    return gv;
}

static int addr_taken(void)
{
    int x = 1, y;
    int *p = &x;
    x = 5;
    *p += 2;
    y = *p;
    return x;
}

static int pointer_alias(void)
{
    int a = 0, b = 0;
    int *p = &a;
    a = 1;
    *p = 2;
    b = 3;
    return a * 10 + b;
}

static int read_after_loop(int n)
{
    int last = 0, i, dead = 0;
    for (i = 0; i < n; i++) {
        last = i * 2;
        dead += last;
    }
    return last;
}

static int shadow(int n)
{
    int t = n;
    {
        int t2 = 1;
        t2 = t2 + n;
    }
    {
        int t2 = 4;
        n = t2 + t;
    }
    return n;
}

static int global_same_name(void)
{
    int r = 0;
    {
        int shared = 9;
        shared = 10;
    }
    shared = 77;
    r = shared;
    return r;
}

static int volatile_local(void)
{
    volatile int v = 1;
    int w;
    v = 2;
    w = vg;
    w = vg;
    vg = 4;
    return 0;
}

static int sizeof_use(void)
{
    int x = 5;
    long y = 100000L * 3;
    float f = 1.5f * gv;
    char big[7];
    x = 6;
    return (int)(sizeof(x) == sizeof(int)) + (int)sizeof(big);
}

static int labels(int n)
{
    int i = 0, dead = 0, live = 0;
again:
    dead = dead + i;
    if (i < n) {
        live += 2;
        i++;
        goto again;
    }
    switch (n) {
    case 1: dead = 1; break;
    case 3: dead = 3; live += 1;
    default: dead = 9;
    }
    return live;
}

static int cond_store(int n)
{
    int x = 0, y = 0, z = 0;
    if (n > 2)
        x = 1;
    else
        y = 2;
    while (n-- > 0)
        z += n;
    do
        x++;
    while (0);
    return n;
}

static int value_use(int n)
{
    int a, b = 0, c;
    if ((a = n) > 3)
        b = 1;
    c = (b = n + 1) + 1;
    return b;
}

static int cascade(int n)
{
    int a = n, b, c, d;
    b = a + 1;
    c = b * 2;
    d = c - a;
    return n;
}

static int ptrloc(int *p)
{
    int *q = p, *r;
    r = q + 1;
    *q = 8;
    return *p;
}


static int ow_init(int n)
{
    int i = 0, s = 0;
    for (i = 0; i < n; i++)
        s += i;
    return s;
}

static int ow_read_between(int n)
{
    int x, y, z;
    x = 1;
    y = x + n;
    x = 2;
    z = x + y;
    return z;
}

static int ow_intervening(int n)
{
    int x = 7, j, t = 0;
    x = n * 2;
    for (j = 0; j < 3; j++)
        t += gv;
    gcount += twice(n);
    x = n + 1;
    return x + t;
}

static int ow_jump(int c)
{
    int x;
    x = 1;
    if (c)
        goto skip;
    x = 2;
skip:
    return x;
}

static int ow_cond(int c)
{
    int x;
    x = 10;
    if (c)
        x = 20;
    return x;
}

static int ow_impure(int n)
{
    int x;
    x = bump();
    x = n;
    return x + gcount;
}

static int ow_return(int c)
{
    int x;
    x = 3;
    if (c)
        return 5;
    x = 4;
    return x;
}

static int ow_loop(int n)
{
    int x = 0, i, r = 0;
    for (i = 0; i < n; i++) {
        r += x;
        x = i;
        x = i * 2;
    }
    return r;
}

static int ow_self(int n)
{
    int x = 1;
    x = 2;
    x = x + n;
    return x;
}

static int ow_switch(int n)
{
    int x = 1;
    switch (n) {
    case 1:
        x = 5;
    case 2:
        x = x + 1;
        break;
    default:
        break;
    }
    return x;
}

static int for_dead(int n)
{
    int i, d, e = 5, g, h = 0;
    for (d = 0; d < n; d++)
        ;
    for (i = 0, g = 1; i < n; i++, e += 2)
        h = i;
    for (h = 1; ; )
        break;
    return i + d;
}

static int multi_decl(int n)
{
    int x = 1, y = x + n, z = 4;
    x = 9;
    z = x + 1;
    return y + z;
}

int main(void)
{
    int arr[1];
    printf("%d\n", sieve());
    { int e = effect_rhs(); printf("%d %d\n", e, gcount); }
    gcount = 0;
    printf("%d\n", chain());
    printf("%d\n", selfref(5));
    printf("%d\n", comma(4));
    gv = 12;
    printf("%d\n", postinc());
    printf("%d\n", addr_taken());
    printf("%d\n", pointer_alias());
    printf("%d\n", read_after_loop(5));
    printf("%d\n", shadow(3));
    printf("%d\n", global_same_name());
    { int e = volatile_local(); printf("%d %d\n", e, vg); }
    printf("%d\n", sizeof_use());
    { int e1 = labels(1), e2 = labels(3), e3 = labels(4); printf("%d %d %d\n", e1, e2, e3); }
    { int e1 = cond_store(1), e2 = cond_store(5); printf("%d %d\n", e1, e2); }
    printf("%d\n", value_use(5));
    printf("%d\n", cascade(7));
    arr[0] = 1;
    { int e = ptrloc(arr); printf("%d %d\n", e, arr[0]); }
    printf("%d %d %d\n", ow_init(5), ow_read_between(3), ow_intervening(4));
    gv = 2;
    printf("%d %d %d %d\n", ow_jump(0), ow_jump(1), ow_cond(0), ow_cond(1));
    gcount = 0;
    { int e = ow_impure(9); printf("%d %d\n", e, gcount); }
    printf("%d %d\n", ow_return(0), ow_return(1));
    printf("%d %d\n", ow_loop(4), ow_self(5));
    printf("%d %d %d\n", ow_switch(1), ow_switch(2), ow_switch(3));
    printf("%d %d %d\n", ow_init(5), ow_read_between(3), ow_intervening(4));
    gv = 2;
    printf("%d %d %d %d\n", ow_jump(0), ow_jump(1), ow_cond(0), ow_cond(1));
    gcount = 0;
    { int e = ow_impure(9); printf("%d %d\n", e, gcount); }
    printf("%d %d\n", ow_return(0), ow_return(1));
    printf("%d %d\n", ow_loop(4), ow_self(5));
    printf("%d %d %d\n", ow_switch(1), ow_switch(2), ow_switch(3));
    printf("%d %d\n", for_dead(4), multi_decl(5));
    return 0;
}
"""

EXPECT = '1\n114 114\n3\n5\n12\n12\n7\n23\n8\n7\n77\n0 4\n8\n2 7 8\n-1 -1\n6\n7\n8 8\n10 6 41\n2 1 10 20\n10 1\n4 5\n6 7\n6 2 1\n10 6 11\n2 1 10 20\n10 1\n4 5\n6 7\n6 2 1\n8 16\n'


def build_run(tmp_path, source, *flags, files=()):
    _, mac = compile_mac(tmp_path, source, *flags, files=files)
    return run_mac(tmp_path, mac)


def pass_log(tmp_path, source, *flags, want_over=False):
    """Function name -> the names that the pass removed (UC80_DSE_DEBUG).
    With ``want_over``: the names of the stores that a later store
    overwrites."""
    c_file = tmp_path / "d.c"
    c_file.write_text(source)
    env = dict(os.environ, UC80_DSE_DEBUG="1")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), *flags, "-o",
         str(tmp_path / "d.mac")],
        capture_output=True, text=True, cwd=str(REPO), env=env)
    assert r.returncode == 0, r.stderr
    out: dict = {}
    over: dict = {}
    for ln in r.stderr.splitlines():
        if ln.startswith("dse: ") and ": dead " in ln:
            fn, _, names = ln[5:].partition(": dead ")
            out.setdefault(fn, set()).update(eval(names))
        elif ln.startswith("dse: ") and ": overwritten " in ln:
            fn, _, name = ln[5:].partition(": overwritten ")
            over.setdefault(fn, set()).add(name)
        elif ln.startswith("dse: ") and "undone" in ln:
            raise AssertionError(ln)
    if want_over:
        return over
    return out


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_programs_match_host(tmp_path, flags):
    assert build_run(tmp_path, SRC, *flags) == EXPECT


def test_pass_removes_the_dead_locals(tmp_path):
    log = pass_log(tmp_path, SRC)
    assert log["sieve"] == {"count"}
    # r stays: twice(r) reads r.  z and b stay: the declaration has
    # several declarators and a call in an initializer.
    assert log["effect_rhs"] == {"a", "c", "q"}
    assert log["chain"] == {"a", "b", "c"}
    assert log["selfref"] == {"x", "y"}
    assert log["comma"] == {"e"}
    assert log["postinc"] == {"x", "y", "w"}
    assert log["addr_taken"] == {"y"}
    assert log["read_after_loop"] == {"dead"}
    assert log["sizeof_use"] == {"y", "f"}
    assert log["labels"] == {"dead"}
    assert log["cond_store"] == {"x", "y", "z"}
    assert log["cascade"] == {"a", "b", "c", "d"}
    assert log["ptrloc"] == {"r"}
    assert log["value_use"] == {"c"}
    assert log["volatile_local"] == {"w"}
    assert log["for_dead"] == {"e", "g", "h"}


def test_pass_leaves_the_others(tmp_path):
    log = pass_log(tmp_path, SRC)
    # addr_taken and pointer_alias: a name under & stays.  volatile_local:
    # v is volatile, and the volatile reads of vg stay.  value_use: the
    # value of an assignment is used.  global_same_name: a global has the
    # name.  selfref: i is read.
    assert "x" not in log["addr_taken"]
    assert "pointer_alias" not in log
    assert "v" not in log["volatile_local"]
    assert not {"a", "b"} & log["value_use"]
    assert "global_same_name" not in log
    assert "i" not in log["selfref"]
    assert "last" not in log["read_after_loop"]


def test_pass_removes_overwritten_stores(tmp_path):
    over = pass_log(tmp_path, SRC, want_over=True)
    # a store that a later store overwrites with nothing between that
    # names the variable, and with no label or jump between
    assert over["ow_init"] == {"i"}
    assert over["ow_intervening"] == {"x"}
    assert over["ow_return"] == {"x"}
    assert over["multi_decl"] == {"z"}
    assert over["global_same_name"] == {"r"}
    # a read between, a jump, a store behind a condition, a read in the
    # new value, a read through a loop or a switch keep the first store
    for fn in ("ow_read_between", "ow_jump", "ow_cond", "ow_self",
               "ow_switch", "ow_loop"):
        assert fn not in over, fn
    # the optimizer of uc_core copies n into x, so x is dead; the call stays
    assert pass_log(tmp_path, SRC)["ow_impure"] == {"x"}
    assert not pass_log(tmp_path, SRC, "--no-dead-store", want_over=True)


def test_switch_turns_the_pass_off(tmp_path):
    assert pass_log(tmp_path, SRC, "--no-dead-store") == {}


def test_asm_and_setjmp_functions_are_left_alone(tmp_path):
    src = r"""
#include <setjmp.h>
jmp_buf jb;
int f1(void) { int x = 1; asm("nop"); x = 2; return 0; }
int f2(void) { int x = 1; if (setjmp(jb)) return x; x = 2; return 0; }
int f3(void) { int x = 1; asm("TIMER_START::"); x = 2; return 0; }
int main(void) { return f1() + f2() + f3(); }
"""
    log = pass_log(tmp_path, src)
    assert "f1" not in log and "f2" not in log
    assert log.get("f3") == {"x"}


def test_sieve_loses_the_count_variable(tmp_path):
    src = """
unsigned char flags[100];
int main(void)
{
    unsigned i, k, count = 98;
    for (i = 2; i < 10; i++)
        if (!flags[i])
            for (k = i * i; k < 100; k += i) {
                count -= !flags[k];
                flags[k] = 1;
            }
    return flags[7];
}
"""
    on, _ = compile_mac(tmp_path, src, "--no-inlining")
    off, _ = compile_mac(tmp_path, src, "--no-inlining", "--no-dead-store")
    assert len(on.splitlines()) < len(off.splitlines())


def test_label_asm_turns_the_overwritten_part_off(tmp_path):
    src = r"""
int g;
int f3(void) { int x = 1; asm("TIMER_START::"); x = 2; return g + x; }
int f4(void) { int x = 1; g = 3; x = 2; return g + x; }
int main(void) { return f3() + f4(); }
"""
    over = pass_log(tmp_path, src, want_over=True)
    assert "f3" not in over
    assert over.get("f4") == {"x"}


@needs_run
def test_two_files_with_the_same_local_and_global_name(tmp_path):
    a = r"""
#include <stdio.h>
int shared_v = 7;
int other(void);
int main(void)
{
    int gb = 3;
    gb = 4;
    printf("%d %d\n", shared_v, other());
    return 0;
}
"""
    b = r"""
extern int shared_v;
int gb = 40;
int other(void)
{
    int shared_w = 5;
    shared_w = 6;
    shared_v += 1;
    return shared_v + gb - 40;
}
"""
    (tmp_path / "b.c").write_text(b)
    got = build_run(tmp_path, a, files=[tmp_path / "b.c"])
    assert got == "8 8\n"


def test_verbose_line_counts_removed_locals(tmp_path):
    c = tmp_path / "v.c"
    c.write_text("int main(void) { int a = 1; int b = 2; a = 3; return b; }\n")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c), "-v", "-o",
         str(tmp_path / "v.mac")],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    assert "Dead locals: 1 removed" in r.stdout + r.stderr


def test_long_flat_function_compiles_fast(tmp_path):
    """The overwritten-store scan is linear in the list length."""
    import time
    n = 1500
    lines = ["int g;", "int main(void)", "{"]
    lines += [f"    int a{i}; a{i} = g + {i};" for i in range(n)]
    lines.append("    int s = 0;")
    lines += [f"    s += a{i};" for i in range(0, n, 7)]
    lines.append("    return s;")
    lines.append("}")
    c = tmp_path / "long.c"
    c.write_text("\n".join(lines) + "\n")

    def timed(*flags):
        t = time.time()
        r = subprocess.run(
            [sys.executable, "-m", "uc80.main", str(c), *flags, "-o",
             str(tmp_path / "l.mac")],
            capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        return time.time() - t

    base = timed("--no-dead-store")
    with_pass = timed()
    assert with_pass < base * 2.5 + 5, (base, with_pass)


NMI_SRC = r"""
#include <z80.h>
#include <stdint.h>
void out(uint8_t port, uint8_t value);
static volatile uint8_t ticks;
static uint8_t total;

#pragma uc80 nmi nmi_isr
void nmi_isr(void)
{
    int junk = 5, extra;
    uint8_t v;
    junk += 3;
    extra = junk * 2;
    v = z80_in(0x00);
    total += v;
    ticks++;
}

int main(void)
{
    int dead = 0, i;
    unsigned char t;
    dead = 9;
    for (i = 0; i < 1; i++)
        dead += i;
    for (;;) {
        t = ticks;
        if (t >= 5)
            break;
    }
    out(0x10, total);
    out(0xFF, 0);
    return 0;
}
"""


def test_nmi_handler_with_dead_locals_in_a_rom_run(tmp_path, monkeypatch):
    import shutil
    from .test_rom_target import _ul80_has_rom_options
    if not (shutil.which("um80") and _ul80_has_rom_options()):
        pytest.skip("needs um80 and a ul80 with ROM options")
    sys.path.insert(0, str(REPO / "bench" / "rom"))
    import isrproof
    ports = []
    for tag, env in (("on", None), ("off", "1")):
        if env:
            monkeypatch.setenv("UC80_NO_DSE", env)
        info = isrproof.build_source(tmp_path / tag, "nmid.c", NMI_SRC)
        assert info["ok"], info["log"]
        n = isrproof.NEOGEO
        m = isrproof.Machine(ram=n["ram"], banks=None, ram_fill="random",
                             sp="poison", track=True)
        m.load_rom(str(info["bin"]))
        m.load_symbols(str(info["sym"]))
        m.magic_ports.add(0xFF)
        m.feed.queue(0x00, [1, 2, 3, 4, 5])
        m.add_nmi_every(6000, start=6000, count=5)
        res = m.run(max_tstates=500_000)
        assert res.reason.startswith("port:"), res.reason
        ports.append(isrproof.ports_of(m, 0, 0xFD))
    assert ports[0] == ports[1] == [(0x10, 15)]
