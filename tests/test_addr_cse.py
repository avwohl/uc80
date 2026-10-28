"""Address CSE and chained constant stores (docs/addr_cse.md).

The pass rewrites the AST: a repeated element address such as ``a[i]``
becomes one pointer local, and ``a = b = K`` becomes ``b = K; a = K``.  The
programs below run in every mode and must print what the host compiler
prints (the expected text comes from a host build, with values that are
clean for a 16-bit int).  The shape tests check that the pass acts where
it is safe, and that it stays out where an assignment, a label, a call, a
volatile access or an address could change the answer.
"""

import os
import subprocess
import sys

import pytest

from .test_register_cc import compile_mac, needs_run, run_mac
from .test_struct_return_abi import REPO

MODES = [
    (), ("--no-addr-cse", "--no-chain-store"), ("--no-addr-cse",),
    ("--no-chain-store",), ("--no-register-locals",), ("--no-shared-storage",),
    ("-O0",), ("--no-inlining",), ("--no-register-cc",), ("--isr-safe",),
]

# local variables as index: every case of a run that must end or not start
ADDR = r"""
#include <stdio.h>
#include <stdint.h>

int ia[16];
long lg[8];
uint8_t ba[300];
struct P { int x; int y; char name[8]; int tab[6]; };
struct P ps[4];
int m2[4][5];
volatile int va[8];
int gi;

static void fill(void)
{
    int i;
    for (i = 0; i < 16; i++) ia[i] = (i * 7 + 3) % 11;
}

static int pass1(int n)
{
    int i, s = 0;
    for (i = 0; i < n; i++) {
        if (ia[i] > ia[i + 1]) {
            int t = ia[i];
            ia[i] = ia[i + 1];
            ia[i + 1] = t;
        }
        s += ia[i] * 3 + ia[i + 1];
    }
    return s;
}

static int barrier(void)
{
    int i = 0;
    ia[i] = 11;
    ia[i] += 1;
    i++;
    ia[i] = 22;
    ia[i] += 2;
    i = i + 1;
    ia[i] = 33;
    ia[i] += 3;
    return ia[0] * 100 + ia[1] * 10 + ia[2];
}

static int viaptr(void)
{
    int i = 2;
    int *q = &i;
    ia[i] = 10;
    *q = 5;
    ia[i] = 20;
    ia[i] += 1;
    return ia[2] * 100 + ia[5];
}

static int elemalias(void)
{
    int i = 3;
    int *p = &ia[3];
    ia[i] = 1;
    *p = 7;
    ia[i] += 2;
    return ia[i] + ia[3];
}

static void poke(void) { ia[4] = 99; gi = 6; }

static int callbetween(void)
{
    int i = 4;
    int r;
    gi = 5;
    ia[i] = 1;
    ia[gi] = 2;
    poke();
    r = ia[i] + ia[gi] * 2;
    ia[i] += 1;
    return r * 10 + ia[4] + ia[gi];
}

static int gotoit(int n)
{
    int i = 1;
    int r = 0;
    ia[i] = 1;
    ia[i] += 2;
    if (n) goto L;
    ia[i] += 10;
L:
    ia[i] += 3;
    r = ia[i];
    ia[i] -= 1;
    return r * 10 + ia[i];
}

static int sw(int k)
{
    int i = k & 3;
    int r = 0;
    switch (k) {
    case 0: r += ia[i]; r += ia[i]; break;
    case 1: r += ia[i] + ia[i + 1];
    case 2: r += ia[i] * 2; r += ia[i + 1]; break;
    default: ia[i] = ia[i] + 5; r = ia[i]; break;
    }
    return r;
}

static int dowhile(void)
{
    int i = 0, r = 0;
    do {
        r += ia[i] + ia[i] * 2;
    } while (++i < 5);
    return r;
}

static int two_d(int n)
{
    int i, j, s = 0;
    for (i = 0; i < 4; i++)
        for (j = 0; j < 4; j++) {
            m2[i][j] = i * 10 + j;
            m2[i][j + 1] = m2[i][j] + 1;
            s += m2[i][j] + m2[i][j + 1] * n;
        }
    return s;
}

static int structs(void)
{
    int i, j, s = 0;
    struct P *p;
    for (i = 0; i < 4; i++) {
        ps[i].x = i + 1;
        ps[i].y = ps[i].x * 2;
        for (j = 0; j < 6; j++) {
            ps[i].tab[j] = ps[i].x + j;
            ps[i].tab[j] += ps[i].y;
        }
    }
    for (p = ps; p < ps + 4; p++) {
        p->tab[1] += p->tab[2];
        p->tab[1] += p->tab[2];
        s += p->tab[1] + p->tab[0];
    }
    return s;
}

static int bytes(void)
{
    uint8_t i = 255;
    int r;
    ba[i + 1] = 7;
    ba[(uint8_t)(i + 1)] = 9;
    ba[i + 1] += 1;
    ba[(uint8_t)(i + 1)] += 1;
    r = ba[256] * 100 + ba[0];
    return r;
}

static int vol(void)
{
    int i = 3, r;
    va[i] = 5;
    r = va[i];
    r += va[i];
    va[i] += 1;
    return r * 10 + va[i];
}

static int rec(int d)
{
    int loc[6];
    int i = d;
    loc[i] = d * 3;
    loc[i] += 1;
    if (d > 0) rec(d - 1);
    loc[i] += 2;
    return loc[i] + loc[i] * 2;
}

static int regparam(int i, int j)
{
    ia[i] = j;
    ia[i] += j;
    ia[j] = i;
    ia[j] += i;
    return ia[i] * 100 + ia[j];
}

static int longidx(long k)
{
    ia[k] = 4;
    ia[k] += 4;
    ia[k + 1] = 1;
    return ia[k] * 10 + ia[k + 1];
}

static int shadow(void)
{
    int i = 1, r = 0;
    ia[i] = 5;
    {
        int i = 2;
        ia[i] = 6;
        ia[i] += 1;
        r += ia[i];
    }
    ia[i] += 1;
    r += ia[i] * 100;
    return r;
}

static int loopw(void)
{
    int i = 0, s = 0;
    while (i < 6) {
        s += ia[i] + ia[i] * 2;
        i += 2;
        s += ia[i];
    }
    return s;
}

static int condexp(int c)
{
    int i = c;
    return (c ? ia[i] : ia[i + 1]) + (c > 1 && ia[i] > 3 ? ia[i] : 0) + ia[i];
}

int main(void)
{
    fill();
    printf("%d\n", pass1(10));
    printf("%d\n", barrier());
    printf("%d\n", viaptr());
    printf("%d\n", elemalias());
    printf("%d\n", callbetween());
    {
        int g0 = gotoit(0), g1 = gotoit(1);
        printf("%d %d\n", g0, g1);
    }
    {
        int s0 = sw(0), s1 = sw(1), s2 = sw(2), s7 = sw(7);
        printf("%d %d %d %d\n", s0, s1, s2, s7);
    }
    fill();
    printf("%d\n", dowhile());
    printf("%d\n", two_d(3));
    printf("%d\n", structs());
    printf("%d\n", bytes());
    printf("%d\n", vol());
    printf("%d\n", rec(3));
    printf("%d\n", regparam(2, 3));
    printf("%d\n", longidx(8L));
    printf("%d\n", shadow());
    fill();
    printf("%d\n", loopw());
    printf("%d %d %d\n", condexp(0), condexp(1), condexp(2));
    return 0;
}
"""

ADDR_EXPECT = '235\n1476\n1021\n18\n1258\n175 65\n24 35 29 14\n90\n1104\n140\n810\n106\n36\n604\n81\n607\n70\n13 20 18\n'

# memory in the key: a load from a named array or a global is an index
ADDR2 = r"""
#include <stdio.h>
#include <stdint.h>

int stack[4];
struct Cell { int discsize; int next; } cell[16];
int freelist;
int ia[16];
int gi;
volatile int vol_one = 1;
int hook_n;

static void init(void)
{
    int i;
    for (i = 0; i < 15; i++) cell[i].next = i + 1;
    cell[15].next = 0;
    freelist = 1;
    for (i = 0; i < 4; i++) stack[i] = 0;
    for (i = 0; i < 16; i++) ia[i] = i * 3 % 7;
}

static int getel(void)
{
    int t = freelist;
    freelist = cell[freelist].next;
    return t;
}

static void push(int d, int s)
{
    int e = getel();
    cell[e].next = stack[s];
    stack[s] = e;
    cell[e].discsize = d;
}

static int pop(int s)
{
    int temp, temp1;
    if (stack[s] > 0) {
        temp1 = cell[stack[s]].discsize;
        temp = cell[stack[s]].next;
        cell[stack[s]].next = freelist;
        freelist = stack[s];
        stack[s] = temp;
        return temp1;
    }
    return -1;
}

static int sum_cells(int s)
{
    int r = 0;
    r += cell[stack[s]].discsize;
    r += cell[stack[s]].next;
    r += cell[stack[s]].discsize * 2;
    return r;
}

static void bump(void) { stack[1] = 3; gi = 2; }

static int call_between(void)
{
    int r;
    stack[1] = 2;
    cell[2].discsize = 5;
    cell[3].discsize = 9;
    r = cell[stack[1]].discsize;
    bump();
    r = r * 100 + cell[stack[1]].discsize;
    r = r * 10 + cell[stack[1]].discsize;
    return r;
}

static int ptr_store(void)
{
    int *p = &stack[2];
    int r;
    stack[2] = 4;
    cell[4].discsize = 7;
    cell[5].discsize = 8;
    r = cell[stack[2]].discsize;
    *p = 5;
    r = r * 10 + cell[stack[2]].discsize;
    r = r * 10 + cell[stack[2]].discsize;
    return r;
}

static int scalar_index(void)
{
    int r = 0;
    int *q = &gi;
    gi = 1;
    r += ia[gi];
    r += ia[gi];
    gi = 2;
    r += ia[gi] * 10;
    r += ia[gi] * 10;
    *q = 3;
    r += ia[gi] * 100;
    r += ia[gi] * 100;
    bump();
    r += ia[gi] * 1000;
    r += ia[gi] * 1000;
    return r;
}

static int vol_between(void)
{
    int r;
    stack[3] = 6;
    cell[6].discsize = 11;
    cell[7].discsize = 12;
    r = cell[stack[3]].discsize;
    if (vol_one) stack[3] = 7;
    r = r * 100 + cell[stack[3]].discsize;
    r = r * 10 + cell[stack[3]].discsize % 10;
    return r;
}

static int local_idx(void)
{
    int loc[4];
    int r = 0;
    loc[0] = 1; loc[1] = 2; loc[2] = 3; loc[3] = 0;
    r += ia[loc[1]];
    r += ia[loc[1]] * 2;
    loc[1] = 5;
    r += ia[loc[1]] * 10;
    r += ia[loc[1]] * 20;
    return r;
}

static int store_same_stmt(void)
{
    int r;
    stack[0] = 2;
    cell[2].next = 9;
    cell[3].next = 8;
    r = cell[stack[0]].next;
    stack[0] = 3;
    r = r * 10 + cell[stack[0]].next;
    return r;
}

static int loop_mem(int n)
{
    int i, r = 0;
    stack[1] = 4;
    for (i = 0; i < n; i++) {
        cell[stack[1]].discsize = i;
        r += cell[stack[1]].discsize + cell[stack[1]].next;
        if (i == 2) stack[1] = 5;
    }
    return r;
}

static int hook(void) { hook_n++; stack[2] = 1; return 1; }

static int cond_call(void)
{
    int r = 0;
    stack[2] = 4;
    cell[1].discsize = 21;
    cell[4].discsize = 40;
    if (cell[stack[2]].discsize > 0 && hook())
        r = cell[stack[2]].discsize;
    r += cell[stack[2]].discsize * 100;
    return r;
}

int main(void)
{
    int a, b;
    init();
    push(5, 0); push(4, 0); push(3, 1);
    printf("%d\n", sum_cells(0));
    printf("%d\n", pop(0));
    printf("%d\n", pop(0));
    printf("%d\n", pop(0));
    printf("%d\n", pop(1));
    printf("%d\n", call_between());
    printf("%d\n", ptr_store());
    init();
    printf("%d\n", scalar_index());
    init();
    printf("%d\n", vol_between());
    printf("%d\n", local_idx());
    printf("%d\n", store_same_stmt());
    init();
    printf("%d\n", loop_mem(5));
    a = cond_call();
    b = hook_n;
    printf("%d %d\n", a, b);
    return 0;
}
"""

ADDR2_EXPECT = '13\n4\n5\n-1\n3\n5099\n788\n12526\n11122\n48\n98\n37\n2121 1\n'

# element types: anonymous struct, typedef, union, enum, bit-field, _Bool
TYPES = r"""
#include <stdio.h>
#include <stdint.h>
struct { int a; int b; } tab[4];
typedef struct { uint8_t x; uint16_t y; } Q;
Q qs[4];
union U { int i; char c[2]; } us[4];
enum E { A, B, C } es[4];
struct BF { unsigned f : 3; unsigned g : 5; } bfs[4];
_Bool flags[8];
const int ct[4] = {10, 20, 30, 40};
char name[3][6] = {"abc", "def", "ghi"};
int f(int i)
{
    int r;
    tab[i].a = i + 1; tab[i].b = tab[i].a * 2; tab[i].a += tab[i].b;
    qs[i].x = i; qs[i].y = qs[i].x + 100; qs[i].x += 3; qs[i].y += qs[i].x;
    us[i].i = 0x0102; us[i].c[1] += 5; us[i].c[0] += us[i].c[1];
    es[i] = B; es[i] = (enum E)(es[i] + 1);
    bfs[i].f = 5; bfs[i].g = 17; bfs[i].f += bfs[i].g;
    flags[i] = 5; flags[i] = flags[i] + 1;
    r = ct[i] + ct[i] + ct[i];
    r += name[i][1] + name[i][2] + name[i][0];
    return r + tab[i].a + tab[i].b + qs[i].x + qs[i].y + us[i].i + es[i] + bfs[i].f + bfs[i].g + flags[i];
}
int main(void)
{
    int i;
    for (i = 0; i < 3; i++) printf("%d\n", f(i));
    return 0;
}
"""

TYPES_EXPECT = '2005\n2052\n2099\n'

CHAIN = r"""
#include <stdio.h>
#include <stdint.h>

signed char sc;
unsigned char uc;
int16_t n;
uint16_t u;
char buf[4];
int arr[4];
long lg;
int *p, *q;
int tgt;
struct B { unsigned f : 3; int g; } bf;
volatile int vv;
volatile int vw;
_Bool bb;

static void show(const char *tag)
{
    printf("%s: sc=%d uc=%u n=%d u=%u buf=%d,%d arr=%d,%d,%d lg=%ld p=%d q=%d f=%u g=%d bb=%d vv=%d vw=%d\n",
           tag, sc, uc, n, u, buf[0], buf[1], arr[0], arr[1], arr[2], lg,
           p == 0, q == 0, bf.f, bf.g, bb, vv, vw);
}

static int locals(int x)
{
    int a, b, c;
    a = b = c = x;
    a += 1;
    return a * 100 + b * 10 + c;
}

int main(void)
{
    int i, j, k;
    sc = uc = n = 5;                 show("a");
    uc = n = 300;                    show("b");   /* n is 300, uc is 44 */
    n = uc = 300;                    show("c");   /* uc is 44, n is 44 */
    sc = n = -5;                     show("d");
    u = n = -1;                      show("e");
    n = u = -1;                      show("f");
    p = q = &tgt;                    show("g");
    p = q = 0;                       show("h");
    arr[0] = arr[1] = arr[2] = 7;    show("i");
    buf[0] = buf[1] = 0;             show("j");
    lg = n = 5;                      show("k");
    lg = n = 30000;                  show("l");
    sc = n = 200;                    show("l2");  /* sc is -56, n is 200 */
    n = sc = 200;                    show("l3");  /* sc is -56, n is -56 */
    bf.f = n = 5;                    show("m");
    n = bf.f = 9;                    show("n");
    bf.g = bf.f = 3;                 show("o");
    vv = vw = n = 3;                 show("p");
    n = vv = vw = 4;                 show("q");
    bb = n = 2;                      show("r");
    n = bb = 2;                      show("s");
    i = j = k = 0;
    if ((i = j = 1) == 1) k = 2;
    printf("%d %d %d\n", i, j, k);
    for (i = j = 0; i < 3; i++, j += 2) k = i + j;
    printf("%d %d %d\n", i, j, k);
    if (k) arr[3] = i = 9;
    else n = i = 8;
    printf("%d %d\n", arr[3], i);
    printf("%d\n", locals(4));
    return 0;
}
"""

CHAIN_EXPECT = 'a: sc=5 uc=5 n=5 u=0 buf=0,0 arr=0,0,0 lg=0 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\nb: sc=5 uc=44 n=300 u=0 buf=0,0 arr=0,0,0 lg=0 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\nc: sc=5 uc=44 n=44 u=0 buf=0,0 arr=0,0,0 lg=0 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\nd: sc=-5 uc=44 n=-5 u=0 buf=0,0 arr=0,0,0 lg=0 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\ne: sc=-5 uc=44 n=-1 u=65535 buf=0,0 arr=0,0,0 lg=0 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\nf: sc=-5 uc=44 n=-1 u=65535 buf=0,0 arr=0,0,0 lg=0 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\ng: sc=-5 uc=44 n=-1 u=65535 buf=0,0 arr=0,0,0 lg=0 p=0 q=0 f=0 g=0 bb=0 vv=0 vw=0\nh: sc=-5 uc=44 n=-1 u=65535 buf=0,0 arr=0,0,0 lg=0 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\ni: sc=-5 uc=44 n=-1 u=65535 buf=0,0 arr=7,7,7 lg=0 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\nj: sc=-5 uc=44 n=-1 u=65535 buf=0,0 arr=7,7,7 lg=0 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\nk: sc=-5 uc=44 n=5 u=65535 buf=0,0 arr=7,7,7 lg=5 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\nl: sc=-5 uc=44 n=30000 u=65535 buf=0,0 arr=7,7,7 lg=30000 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\nl2: sc=-56 uc=44 n=200 u=65535 buf=0,0 arr=7,7,7 lg=30000 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\nl3: sc=-56 uc=44 n=-56 u=65535 buf=0,0 arr=7,7,7 lg=30000 p=1 q=1 f=0 g=0 bb=0 vv=0 vw=0\nm: sc=-56 uc=44 n=5 u=65535 buf=0,0 arr=7,7,7 lg=30000 p=1 q=1 f=5 g=0 bb=0 vv=0 vw=0\nn: sc=-56 uc=44 n=1 u=65535 buf=0,0 arr=7,7,7 lg=30000 p=1 q=1 f=1 g=0 bb=0 vv=0 vw=0\no: sc=-56 uc=44 n=1 u=65535 buf=0,0 arr=7,7,7 lg=30000 p=1 q=1 f=3 g=3 bb=0 vv=0 vw=0\np: sc=-56 uc=44 n=3 u=65535 buf=0,0 arr=7,7,7 lg=30000 p=1 q=1 f=3 g=3 bb=0 vv=3 vw=3\nq: sc=-56 uc=44 n=4 u=65535 buf=0,0 arr=7,7,7 lg=30000 p=1 q=1 f=3 g=3 bb=0 vv=4 vw=4\nr: sc=-56 uc=44 n=2 u=65535 buf=0,0 arr=7,7,7 lg=30000 p=1 q=1 f=3 g=3 bb=1 vv=4 vw=4\ns: sc=-56 uc=44 n=1 u=65535 buf=0,0 arr=7,7,7 lg=30000 p=1 q=1 f=3 g=3 bb=1 vv=4 vw=4\n1 1 2\n3 6 6\n9 9\n544\n'

# three equal statements (one macro, one line): the temp goes before the
# right one, not before the first equal statement
DUP = r"""
#include <stdio.h>
int ia[8];
#define BUMP ia[i] += 1; ia[i] += 2; ia[i] += 3;
int f(void)
{
    int i = 1, r = 0;
    ia[1] = ia[2] = ia[3] = 0;
    BUMP i++; BUMP i++; BUMP
    r = ia[1] * 100 + ia[2] * 10 + ia[3];
    return r;
}
int main(void) { printf("%d\n", f()); return 0; }
"""


def build_run(tmp_path, source, *flags):
    _, mac = compile_mac(tmp_path, source, *flags)
    return run_mac(tmp_path, mac)


def pass_log(tmp_path, source, *flags):
    """The lines that the pass writes with UC80_ACSE_DEBUG=1."""
    c_file = tmp_path / "d.c"
    c_file.write_text(source)
    env = dict(os.environ, UC80_ACSE_DEBUG="1")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), *flags, "-o",
         str(tmp_path / "d.mac")],
        capture_output=True, text=True, cwd=str(REPO), env=env)
    assert r.returncode == 0, r.stderr
    return [ln[6:] for ln in r.stderr.splitlines() if ln.startswith("acse: ")]


def temp_functions(log):
    return {ln.split(":")[0] for ln in log if " key=" in ln}


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_address_programs_match_host(tmp_path, flags):
    assert build_run(tmp_path, ADDR, *flags) == ADDR_EXPECT


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_memory_key_programs_match_host(tmp_path, flags):
    assert build_run(tmp_path, ADDR2, *flags) == ADDR2_EXPECT


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_element_types_match_host(tmp_path, flags):
    assert build_run(tmp_path, TYPES, *flags) == TYPES_EXPECT


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_chain_programs_match_host(tmp_path, flags):
    assert build_run(tmp_path, CHAIN, *flags) == CHAIN_EXPECT


@needs_run
@pytest.mark.parametrize("flags", MODES)
def test_equal_statements(tmp_path, flags):
    assert build_run(tmp_path, DUP, *flags).split() == ["666"]


def test_pass_acts_where_it_is_safe(tmp_path):
    fns = temp_functions(pass_log(tmp_path, ADDR))
    for name in ("pass1", "elemalias", "callbetween", "gotoit",
                 "two_d", "structs", "rec", "regparam", "longidx",
                 "shadow", "condexp"):
        assert name in fns, name


def test_pass_stays_out_where_a_variable_can_change(tmp_path):
    fns = temp_functions(pass_log(tmp_path, ADDR))
    # barrier: i is assigned between the uses; viaptr: &i is taken;
    # vol: a volatile array; loopw and dowhile: i changes inside the loop;
    # sw: every access is behind a branch of the switch
    for name in ("barrier", "viaptr", "vol", "loopw", "dowhile", "bytes",
                 "sw"):
        assert name not in fns, name


def test_memory_keys_end_at_stores_and_calls(tmp_path):
    log = pass_log(tmp_path, ADDR2)
    by_fn = {}
    for ln in log:
        if " key=" in ln:
            by_fn.setdefault(ln.split(":")[0], []).append(ln)
    # pop and sum_cells: cell[stack[s]] three times, no store to stack
    for name in ("pop", "sum_cells"):
        assert any("g:stack" in ln for ln in by_fn[name]), name
    # one temp only in these: the store or the call ends the first run
    assert len(by_fn["call_between"]) == 1
    assert len(by_fn["ptr_store"]) == 1
    assert len(by_fn["vol_between"]) == 1
    # cond_call: the call in the condition ends the run
    assert "cond_call" not in by_fn


ROM_SRC = """
#include <stdint.h>
int stack[4];
struct Cell { int a; int b; } cell[16];
volatile int ticks;
int out;
%s
int sum(int s)
{
    int r = cell[stack[s]].a;
    r += cell[stack[s]].b;
    r += cell[stack[s]].a * 2;
    return r;
}
int main(void)
{
    out = sum(1);
    return 0;
}
"""


def rom_keys(tmp_path, isr):
    c_file = tmp_path / "i.c"
    c_file.write_text(ROM_SRC % (
        "#pragma uc80 interrupt isr\nvoid isr(void) { ticks++; }" if isr
        else "void isr(void) { ticks++; }"))
    env = dict(os.environ, UC80_ACSE_DEBUG="1")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), "--target", "rom",
         "-o", str(tmp_path / "i.mac")],
        capture_output=True, text=True, cwd=str(REPO), env=env)
    assert r.returncode == 0, r.stderr
    return [ln for ln in r.stderr.splitlines() if " key=" in ln]


def test_no_memory_keys_with_an_interrupt_function(tmp_path):
    # the interrupt can change stack[]: the key would hold a stale load
    assert any("g:stack" in ln for ln in rom_keys(tmp_path, False))
    assert not any("g:stack" in ln for ln in rom_keys(tmp_path, True))


def test_no_memory_keys_with_a_vector_only_handler(tmp_path):
    # a handler bound with '#pragma uc80 vector' (not an interrupt function
    # for the compiler) can change stack[] too
    c_file = tmp_path / "v.c"
    c_file.write_text(ROM_SRC % (
        "#pragma uc80 vector 38h isr\nvoid isr(void) { ticks++; }"))
    env = dict(os.environ, UC80_ACSE_DEBUG="1")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), "--target", "rom",
         "-o", str(tmp_path / "v.mac")],
        capture_output=True, text=True, cwd=str(REPO), env=env)
    assert r.returncode == 0, r.stderr
    assert not any(" key=" in ln and "g:stack" in ln
                   for ln in r.stderr.splitlines())


def test_switches_turn_the_pass_off(tmp_path):
    assert pass_log(tmp_path, ADDR, "--no-addr-cse") == [
        ln for ln in pass_log(tmp_path, ADDR, "--no-addr-cse")
        if " key=" not in ln]
    assert not any("chain" in ln for ln in
                   pass_log(tmp_path, CHAIN, "--no-chain-store"))
    assert any("chain" in ln for ln in pass_log(tmp_path, CHAIN))


def test_chain_is_shorter_and_has_no_exchange(tmp_path):
    src = """
unsigned char col[8], d1a[8], d2a[8];
void f(unsigned char c, unsigned char d1, unsigned char d2)
{
    col[c] = d1a[d1] = d2a[d2] = 1;
}
int main(void) { f(1, 2, 3); return col[1]; }
"""
    on, _ = compile_mac(tmp_path, src, "--no-inlining")
    off, _ = compile_mac(tmp_path, src, "--no-inlining", "--no-chain-store")
    def body(m):
        s = m.index("\n_f:")
        return m[s:m.index("@f_ret:", s)]
    assert len(body(on).splitlines()) < len(body(off).splitlines())
    assert "ex\tDE,HL" not in body(on)
