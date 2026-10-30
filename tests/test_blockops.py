"""Inline block operations (docs/blockops.md).

The program runs in every mode and must print what the host compiler
prints.  It fills and copies with constant sizes of 1 to 400 bytes at
global, pointer, local and string addresses, with side effects in the
arguments, with a used result, with overlapping memmove, through volatile
objects, and with aliasing pointers.  The shape tests check which calls
become inline code and which calls stay.
"""

import pytest

from .test_register_cc import compile_mac, func, needs_run, run_mac

MODES = [
    (), ("--no-blockops",), ("--no-addr-cse",), ("--no-register-locals",),
    ("--no-shared-storage",), ("-O0",), ("--no-inlining",),
    ("--no-register-cc",), ("--isr-safe",), ("--no-ast-optimize",),
    ("--no-dead-store",),
]

SRC = r"""#include <stdio.h>
#include <string.h>
typedef unsigned short U;

unsigned char g[64], h[64], gg[400];
volatile unsigned char vg[16];
struct S3 { char a, b, c; };
struct S4 { short a, b; };
struct S7 { char a[7]; };
struct S20 { short a[10]; };
struct S3 s3a = {1, 2, 3}, s3b;
struct S4 s4a = {10, 20}, s4b;
struct S7 s7a = {{1, 2, 3, 4, 5, 6, 7}}, s7b;
struct S20 s20a, s20b;

static U sum(const unsigned char *p, int n)
{
    U s = 0;
    int i;
    for (i = 0; i < n; i++)
        s = s * 31 + p[i] + 1;
    return s;
}

static void clear(void) { int i; for (i = 0; i < 64; i++) g[i] = h[i] = 0x5A; }

static U fills(void)
{
    U r = 0;
    int k = 9;
    clear(); memset(g + 4, 0, 1); r += sum(g, 64);
    clear(); memset(g + 4, 0, 2); r += sum(g, 64);
    clear(); memset(g + 4, 0, 3); r += sum(g, 64);
    clear(); memset(g + 4, 0, 4); r += sum(g, 64);
    clear(); memset(g + 4, 0, 5); r += sum(g, 64);
    clear(); memset(g, 0xAB, 1); r += sum(g, 64);
    clear(); memset(g, 0xAB, 2); r += sum(g, 64);
    clear(); memset(g, 0xAB, 3); r += sum(g, 64);
    clear(); memset(g, 0xAB, 4); r += sum(g, 64);
    clear(); memset(g, 0xAB, 9); r += sum(g, 64);
    clear(); memset(g + 1, 300 + 7, 33); r += sum(g, 64);
    clear(); memset(g + 1, k, 1); r += sum(g, 64);
    clear(); memset(g + 1, k, 3); r += sum(g, 64);
    clear(); memset(g + 1, k + 1, 4); r += sum(g, 64);
    clear(); memset(g + 1, k + 2, 20); r += sum(g, 64);
    clear(); memset(g, 0, 64); r += sum(g, 64);
    memset(gg, 0x11, 300); r += sum(gg, 400);
    memset(gg, 0, sizeof gg); r += sum(gg, 400);
    memset(gg + 3, -1, 256); r += sum(gg, 400);
    return r;
}

static U fillp(unsigned char *p, int v)
{
    U r = 0;
    clear(); memset(p + 2, 0, 1); r += sum(g, 64);
    clear(); memset(p + 2, 0, 2); r += sum(g, 64);
    clear(); memset(p + 2, 0, 3); r += sum(g, 64);
    clear(); memset(p + 2, 7, 4); r += sum(g, 64);
    clear(); memset(p + 2, 7, 5); r += sum(g, 64);
    clear(); memset(p + 2, v, 1); r += sum(g, 64);
    clear(); memset(p + 2, v, 4); r += sum(g, 64);
    clear(); memset(p + 2, v, 6); r += sum(g, 64);
    clear(); memset(p, v++, 6); memset(p + 8, v++, 2); r += sum(g, 64) + v;
    return r;
}

static U copies(unsigned char *d, const unsigned char *s)
{
    U r = 0;
    int i;
    for (i = 0; i < 64; i++) h[i] = i * 3 + 1;
    clear(); memcpy(g + 1, h + 2, 1); r += sum(g, 64);
    clear(); memcpy(g + 1, h + 2, 2); r += sum(g, 64);
    clear(); memcpy(g + 1, h + 2, 3); r += sum(g, 64);
    clear(); memcpy(g + 1, h + 2, 4); r += sum(g, 64);
    clear(); memcpy(g + 1, h + 2, 5); r += sum(g, 64);
    clear(); memcpy(g, h, 40); r += sum(g, 64);
    for (i = 0; i < 64; i++) h[i] = i * 3 + 1;
    clear(); memcpy(d + 1, s + 2, 1); r += sum(g, 64);
    clear(); memcpy(d + 1, s + 2, 3); r += sum(g, 64);
    clear(); memcpy(d + 1, s + 2, 4); r += sum(g, 64);
    clear(); memcpy(d + 1, s + 2, 17); r += sum(g, 64);
    clear(); memcpy(d + 1, "hello", 5); r += sum(g, 64);
    clear(); memcpy(d + 1, "hello", 6); r += sum(g, 64);
    return r;
}

static U sidefx(void)
{
    unsigned char *p = g, *q = h;
    U r;
    int n = 0;
    clear();
    memset(p++, 1, 3); memset(p++, 2, 8);
    memcpy(q++, p++, 6); memcpy(q++, p++, 2);
    r = sum(g, 64) + sum(h, 64) + (p - g) + (q - h);
    return r;
}

static U usedres(void)
{
    unsigned char *q;
    clear();
    q = memset(g + 3, 9, 5);
    r_dummy: ;
    return sum(g, 64) + (q - g) + (U)(memcpy(h + 1, g, 8) - (void *)h);
}

static U moves(void)
{
    U r = 0;
    int i;
    for (i = 0; i < 64; i++) g[i] = i + 1;
    memmove(g + 2, g, 10); r += sum(g, 64);
    for (i = 0; i < 64; i++) g[i] = i + 1;
    memmove(g, g + 2, 10); r += sum(g, 64);
    for (i = 0; i < 64; i++) g[i] = i + 1;
    memmove(g + 1, g, 3); r += sum(g, 64);
    return r;
}

static U vols(void)
{
    volatile unsigned char *vp = vg;
    U r = 0;
    int i;
    memset((void *)vg, 3, 8);
    memset((void *)vp, 4, 6);
    memcpy((void *)vg, g, 5);
    for (i = 0; i < 16; i++) r = r * 7 + vg[i];
    return r;
}

static U alias(void)
{
    unsigned char *p = g, *q = g + 4;
    U r;
    clear();
    *p = 1; *q = 2;
    memcpy(g + 8, p, 6);
    p[1] = 77;
    memset(q, 0, 2);
    r = sum(g, 64);
    q[0] = p[0] + 40;
    memcpy(h, q, 3);
    return r + sum(h, 64);
}

static U rec(int n)
{
    unsigned char loc[40];
    unsigned char two[8];
    U r;
    memset(loc, n, 40);
    memset(two, 0, sizeof two);
    two[1] = n;
    if (n > 0) r = rec(n - 1); else r = 0;
    memcpy(g, loc, 20);
    return r + loc[7] + sum(g, 20) + sum(two, 8);
}

static U zeroinit(int n)
{
    unsigned char a[50] = {0};
    unsigned char b[6] = {1};
    unsigned char c[9] = "abc";
    int i;
    for (i = 0; i < n; i++) { a[i] = i; b[i % 6]++; }
    return sum(a, 50) + sum(b, 6) + sum(c, 9);
}

static U structs(void)
{
    U r = 0;
    struct S3 l3 = s3a;
    struct S4 l4;
    struct S7 l7;
    struct S20 l20;
    int i;
    for (i = 0; i < 10; i++) s20a.a[i] = i * 100;
    s3b = s3a; s4b = s4a; s7b = s7a; s20b = s20a;
    l4 = s4a; l7 = s7a; l20 = s20a;
    r += s3b.a + s3b.b + s3b.c + l3.c;
    r += s4b.a + s4b.b + l4.b;
    r += sum((unsigned char *)&s7b, 7) + sum((unsigned char *)&l7, 7);
    r += sum((unsigned char *)&s20b, 20) + sum((unsigned char *)&l20, 20);
    l3.a = 9; s3b = l3; r += s3b.a;
    return r;
}

int main(void)
{
    static unsigned char gsrc[64];
    int i;
    for (i = 0; i < 64; i++) gsrc[i] = i ^ 0x35;
    printf("%u\n", fills());
    printf("%u\n", fillp(g, 5));
    printf("%u\n", fillp(h, 200));
    printf("%u\n", copies(g, gsrc));
    printf("%u\n", sidefx());
    printf("%u\n", usedres());
    printf("%u\n", moves());
    printf("%u\n", vols());
    printf("%u\n", alias());
    printf("%u\n", rec(3));
    printf("%u %u\n", zeroinit(5), zeroinit(40));
    printf("%u\n", structs());
    return 0;
}
"""

EXPECT = '38802\n61539\n19658\n46550\n2687\n55027\n29695\n22920\n12340\n41484\n14815 5142\n59392\n'


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_programs_match_host(tmp_path, flags):
    _, mac = compile_mac(tmp_path, SRC, *flags)
    assert run_mac(tmp_path, mac) == EXPECT


LOOPS = r"""#include <stdio.h>
#include <string.h>
typedef unsigned short U;
struct s4 { unsigned char b[4]; };
struct s3 { unsigned char b[3]; };
struct s4 g4[4];
struct s3 g3[4];
unsigned char buf[32];

static U sum_bytes(const unsigned char *p, U len)
{
    U s = 0, i;
    for (i = 0; i < len; i++)
        s = (U)(s * 31u + p[i]);
    return s;
}

static U copy4(U n)
{
    U i;
    for (i = 0; i < n; i++) {
        unsigned char k = (unsigned char)(i & 3);
        g4[k] = g4[(k + 1) & 3];
        g4[k].b[i & 3] = (unsigned char)(g4[k].b[i & 3] + (unsigned char)i);
    }
    return sum_bytes(&g4[0].b[0], sizeof g4);
}

static U copy3(U n)
{
    U i;
    for (i = 0; i < n; i++) {
        unsigned char k = (unsigned char)(i & 3);
        g3[k] = g3[(k + 2) & 3];
        g3[k].b[i % 3] = (unsigned char)(g3[k].b[i % 3] + (unsigned char)i);
    }
    return sum_bytes(&g3[0].b[0], sizeof g3);
}

static U mcopy(U n, unsigned char *p, const unsigned char *q)
{
    U i, j, t = 0;
    for (i = 0; i < n; i++) {
        memcpy(p + (i & 7), q + (i & 3), 3);
        memcpy(buf + (i & 15), p, 4);
        memset(buf + 16 + (i & 7), (int)i, 2);
        for (j = 0; j < 2; j++)
            t += buf[(i + j) & 31];
    }
    return t + sum_bytes(buf, 32) + i + j;
}

int main(void)
{
    unsigned char a[16], b[16];
    int i;
    for (i = 0; i < 4; i++) { memset(&g4[i], i + 1, 4); memset(&g3[i], 9 - i, 3); }
    for (i = 0; i < 16; i++) { a[i] = i * 5; b[i] = i * 7 + 1; }
    printf("%u\n", copy4(100));
    printf("%u\n", copy3(100));
    printf("%u\n", mcopy(50, a, b));
    return 0;
}
"""


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_loops_with_register_locals_around_ldi(tmp_path, flags):
    _, mac = compile_mac(tmp_path, LOOPS, *flags)
    assert run_mac(tmp_path, mac) == '22210\n16861\n22758\n'


def test_loop_variable_stays_in_bc_around_ldi(tmp_path):
    mac, _ = compile_mac(tmp_path, LOOPS, "--no-inlining")
    body = func(mac, "copy4")
    assert "\tldi\n" in body
    # the loop counter is in BC: no reload of the counter from memory
    assert body.count("push\tBC") <= 2
    assert "inc\tBC" in body


SHAPES = r"""
#include <string.h>
char a[100], b[100];
volatile char va[10];
void ptr(char *d, char *s, int n, int v)
{
    memset(a, 0, 50);
    memcpy(b, a, 30);
    memset(d, v, 7);
    memcpy(d, s, 2);
    memset(d, 1, n);
    memcpy(d, s, n);
    memmove(d, s, 10);
    memset(va, 0, 10);
    memcpy(d, s, 0);
    d = memset(a, 1, 9);
    d = memcpy(a, b, 9);
}
int main(void) { ptr(a, b, 3, 4); return a[1]; }
"""


def calls_of(body):
    return [ln.split()[1] for ln in body.splitlines()
            if ln.strip().startswith("call")]


def test_inline_and_calls_in_one_function(tmp_path):
    mac, _ = compile_mac(tmp_path, SHAPES, "--no-inlining")
    body = func(mac, "ptr")
    # kept: variable size, memmove, size 0, two used results.  The fill of
    # the volatile array is a store loop, not a call (docs/blockops.md).
    assert calls_of(body) == ["_memset", "_memcpy", "_memmove",
                              "_memcpy", "_memset", "_memcpy"]
    assert "djnz" in body
    assert body.count("ldir") == 3
    assert body.count("\tldi\n") == 2


def test_switch_turns_the_pass_off(tmp_path):
    mac, _ = compile_mac(tmp_path, SHAPES, "--no-inlining", "--no-blockops")
    calls = calls_of(func(mac, "ptr"))
    assert calls.count("_memset") == 5 and calls.count("_memcpy") == 5


def test_small_sizes_store_directly(tmp_path):
    src = """#include <string.h>
char g[8]; char h[8];
void f(void) { memset(g, 0, 2); memset(h, 5, 1); memcpy(g, h, 2); }
int main(void) { f(); return g[0]; }
"""
    mac, _ = compile_mac(tmp_path, src, "--no-inlining")
    body = func(mac, "f")
    assert "ldir" not in body and "call" not in body
    assert "ld\t(_g),HL" in body
    assert body.count("\tldi\n") == 2


USER_NAMES = r"""#include <stdio.h>
static void memset(int *p, int v, int n) { while (n--) *p++ = v; }
static void memcpy(int *d, const int *s, int n) { while (n--) *d++ = *s++; }
int w[8], v[8] = {1, 2, 3, 4, 5, 6, 7, 8};
int main(void)
{
    memset(w, 7, 8);
    memcpy(w + 1, v, 4);
    printf("%d %d %d %d\n", w[0], w[1], w[5], w[7]);
    return 0;
}
"""


@needs_run
def test_program_defined_memset_is_not_inlined(tmp_path):
    for flags in ((), ("--no-inlining",), ("-O0",)):
        text, mac = compile_mac(tmp_path, USER_NAMES, *flags)
        assert run_mac(tmp_path, mac) == "7 1 7 7\n"
        assert "ldi" not in func(text, "main")


FILLS1 = r"""#include <stdio.h>
#include <string.h>
unsigned char g[16];
int main(void)
{
    int i;
    memset(g + 3, 9, 1);
    memset(g + 8, 4, 5);
    memcpy(g + 1, g + 3, 1);
    for (i = 0; i < 16; i++) printf("%d ", g[i]);
    printf("\n");
    return 0;
}
"""


@needs_run
@pytest.mark.parametrize("unroll", ["0", "1", "2", "8"])
def test_unroll_limit_variable(tmp_path, monkeypatch, unroll):
    monkeypatch.setenv("UC80_BLOCKOP_UNROLL", unroll)
    _, mac = compile_mac(tmp_path, FILLS1)
    assert run_mac(tmp_path, mac) == "0 9 0 9 0 0 0 0 4 4 4 4 4 0 0 0 \n"
