"""Family K (link size, code size) together with bug round 6 and the
integrate13 repair: the interactions that only a program that RUNS shows.

(a) BENCH_CHECK do loops with the unroller, the for-entry loops and the
    rotation budget.
(b) --int=32 results of 16-bit operands with the short helpers of family K
    (__div16b, compact __div16, __sdiv16m, __mul16c, __shr32c, __div32w)
    and the unroller.
(c) The token-based preprocessor with the printf literal union
    (printf_hook.union_is_safe): macros that expand to formats, # and ##
    built formats, __VA_ARGS__ wrappers, a format in a variable.
(d) volatile in the legacy types (typedef, struct member) with the
    volatile refusal of the unroller, the forwarding of peep_flow and the
    fill idiom.

The expected output is the output of the host compiler.
"""

import subprocess

import pytest

from .test_famj_kernels import run_raw, ids
from .test_famj_startup import compile_c, REPO
from .test_struct_return_abi import _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")


def host(tmp_path, src):
    c = tmp_path / "host.c"
    c.write_text(src)
    exe = tmp_path / "host"
    r = subprocess.run(["cc", "-w", f"-I{REPO / 'bench'}", "-o", str(exe),
                        str(c)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        pytest.skip("no host compiler")
    return subprocess.run([str(exe)], capture_output=True, text=True,
                          timeout=120).stdout


def uc(tmp_path, src, *flags):
    mac = compile_c(tmp_path, src, f"-I{REPO / 'bench'}", *flags)
    return (run_raw(tmp_path, mac).decode("latin-1").replace("\r", ""),
            mac.read_text())


# ---- (a) check loops, unroller, for-entry -------------------------------

A_SRC = r"""
#include <stdio.h>
#include "bench.h"

unsigned g, gi;
unsigned char tab[4][3];

int main(void)
{
    unsigned i, j, s = 0;
    for (i = 0; i < 4; i++)
        for (j = 0; j < 3; j++)
            tab[i][j] = (unsigned char)(i * 3 + j);
    for (i = 0; i < 100; i++)
        s += i;
    for (gi = 0; gi < 50; gi++)
        g += gi;
    for (i = 3; i < 40; i += 3)
        s += tab[i & 3][i % 3];
    BENCH_CHECK_EQ(s, 5013);
    BENCH_CHECK_NEAR(g, 1225, 2);
    BENCH_CHECK(g == 1225);
    printf("%u %u %u %u\n", s, g, (unsigned)tab[3][2], i);
    return 0;
}
"""

ALL_OFF = ("--no-for-entry", "--no-loop-unroll")


@pytest.mark.parametrize(
    "flags", [(), ("-O0",), ("--int=32",), ALL_OFF, ("--no-loop-unroll",),
              ("--no-for-entry",)], ids=ids)
def test_check_loops_with_new_loop_passes(tmp_path, flags):
    want = host(tmp_path, A_SRC)
    out, mac = uc(tmp_path, A_SRC, *flags)
    assert out == want
    if flags == ():
        # the global counter keeps its rotated loop, and the three check
        # loops never take rotation budget
        assert "@RB" in mac


# ---- (b) --int=32 with the short helpers --------------------------------

B_SRC = r"""
#include <stdio.h>
#include <stdint.h>

unsigned short us = 40000;
short s = -32768, m1 = -1, s2 = -1234;
uint32_t ul = 0x70F0F0F0UL;
int n = 0, four = 4;

int main(void)
{
    unsigned i;
    int32_t acc = 0;
    printf("%ld %ld %ld %ld\n", (long)(us / 7), (long)(us % 200),
           (long)(us / 255), (long)(us % 1));
    printf("%ld %ld\n", (long)(s2 / 7), (long)(s2 % 7));
    printf("%lu %lu %lu\n", (unsigned long)(ul / us), (unsigned long)(ul % us), (unsigned long)(ul >> (four * 2)));
    printf("%lu %lu\n", (unsigned long)(ul >> (n + 4)), (unsigned long)(ul >> 4));
    for (i = 0; i < 4; i++)
        acc += (long)((us >> n) + i) + us / (n + 1) + (long)(ul >> (i * 4));
    printf("%ld\n", (long)acc);
    for (i = 1; i < 4; i++)
        acc += s2 / (int)i + s2 % (int)i;
    printf("%ld %ld\n", (long)acc, (long)((uint32_t)us * 3UL / 7));
    return 0;
}
"""

B32_SRC = r"""
#include <stdio.h>
#include <stdint.h>

unsigned short us = 40000;
short s = -32768, m1 = -1;
uint32_t ul = 0x70F0F0F0UL;
int n = 0, four = 4;

int main(void)
{
    int i;
    int32_t acc = 0;
    printf("%d %d %d %d\n", (us >> n) + 1, s / m1, -(s >> 1), ~(us >> n));
    printf("%d %d\n", us / 7 + 1, us % 200 - 300000);
    printf("%lu %lu\n", (unsigned long)(ul >> (four * 2)), (unsigned long)(ul / us));
    for (i = 0; i < 4; i++)
        acc += (us >> n) + i + us / (n + 1) + (ul >> (i * 4));
    printf("%ld\n", (long)acc);
    return 0;
}
"""

K_OFF = ("--no-printf-to-putchar", "--no-printf-literal-union",
         "--no-div32w", "--no-qsort-small", "--no-div16b", "--no-const-store",
         "--no-mul16c", "--no-sdivmod-merge", "--no-for-entry",
         "--no-puts-small", "--no-div16-compact", "--no-shr32-loop",
         "--no-loop-unroll", "--no-flow-opt", "--no-branch-merge",
         "--no-drop-printf-tables", "--no-fast-bss-clear")
B_FLAGS = [(), ("--size-helpers",), ("--no-for-entry", "--no-loop-unroll"),
           ("-O0",), ("--no-div16b",), ("--no-div16-compact",),
           ("--no-shr32-loop", "--no-div32w"), K_OFF]


def _supported(tmp_path, flag):
    r = subprocess.run(
        ["python3", "-m", "uc80.main", "--help"], capture_output=True,
        text=True, cwd=str(REPO))
    return flag.split("=")[0] in r.stdout


@pytest.mark.parametrize("flags", B_FLAGS, ids=ids)
@pytest.mark.parametrize("bits", ["--int=16", "--int=32"])
def test_int32_and_short_helpers(tmp_path, flags, bits):
    for f in flags:
        if not _supported(tmp_path, f):
            pytest.skip(f"no switch {f}")
    want = host(tmp_path, B_SRC)
    out, _ = uc(tmp_path, B_SRC, bits, *flags)
    assert out == want


@pytest.mark.parametrize("flags", B_FLAGS[:4] + B_FLAGS[-1:], ids=ids)
def test_int32_results_with_short_helpers(tmp_path, flags):
    want = host(tmp_path, B32_SRC)
    out, _ = uc(tmp_path, B32_SRC, "--int=32", *flags)
    assert out == want


# ---- (c) token preprocessor and the printf literal union ----------------

C_SRC = r"""
#include <stdio.h>
#include <stdarg.h>

#define FMT "%d|%s\n"
#define MK(c) "%" #c "\n"
#define CAT(a, b) a ## b
#define fmt2 "%s+%d\n"
#define PRE "%l"
#define LOG(...) logf_(__VA_ARGS__)
#define SHOW(x) printf(#x " = %d\n", x)

static void logf_(const char *f, ...)
{
    va_list ap;
    va_start(ap, f);
    vprintf(f, ap);
    va_end(ap);
}

int main(void)
{
    int v = 42;
    long l = 1234567L;
    printf(FMT, v, "x");
    printf(MK(d), v);
    printf(MK(x), v);
    printf(PRE "d\n", l);
    printf(CAT(fmt, 2), "joined", 5);
    LOG("%d %s\n", v, "wrapped");
    LOG(PRE "u\n", (unsigned long)l);
    SHOW(v + 1);
    return 0;
}
"""

C2_SRC = r"""
#include <stdio.h>

int main(void)
{
    const char *f = "%lu %x\n";
    char buf[16];
    unsigned long ul = 4000000000UL;
    buf[0] = '%'; buf[1] = 'o'; buf[2] = '\n'; buf[3] = 0;
    printf(f, ul, 255);
    printf(buf, 8);
    printf("%d\n", 7);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("--no-printf-literal-union",), K_OFF, ("-O0",),
                                   ("--int=32",)], ids=ids)
@pytest.mark.parametrize("src", [C_SRC, C2_SRC], ids=["macros", "variable"])
def test_printf_union_with_macros(tmp_path, flags, src):
    for f in flags:
        if not _supported(tmp_path, f):
            pytest.skip(f"no switch {f}")
    want = host(tmp_path, src)
    out, _ = uc(tmp_path, src, *flags)
    assert out == want


# ---- (d) volatile in legacy types ---------------------------------------

D_SRC = r"""
typedef volatile unsigned char vreg;
struct S { volatile int x; int y; };
typedef volatile int vint;
vreg v; vint w; struct S st; volatile unsigned char buf[8];
int g1, g2, sum;
int main(void)
{
    unsigned char i;
    for (v = 0; v < 3; v++) sum += 2;
    for (w = 0; w < 4; w++) sum += 3;
    for (i = 0; i < 8; i++) buf[i] = 0;
    st.x = 5; g1 = st.x; g2 = st.x;
    for (st.x = 0; st.x < 3; st.x++) sum++;
    return sum + g1 + g2;
}
"""

D_RUN = r"""
#include <stdio.h>
typedef volatile unsigned char vreg;
typedef volatile int vint;
vreg v; vint w; volatile unsigned char buf[8];
int sum;
int main(void)
{
    unsigned char i;
    int k;
    for (i = 0; i < 8; i++) buf[i] = (unsigned char)(i + 1);
    for (v = 0; v < 3; v++) sum += 2;
    for (w = 0; w < 4; w++) sum += buf[w];
    for (k = 0; k < 4; k++) buf[k] = 0;
    printf("%d %d %d %d %d\n", sum, v, w, buf[3], buf[7]);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("-O0",), ("--int=32",)], ids=ids)
def test_volatile_counters_are_not_unrolled(tmp_path, flags):
    mac = compile_c(tmp_path, D_SRC, *flags)
    text = mac.read_text()
    body = text[text.index("_main:"):]
    # three volatile counters keep their loops: three tests back
    loops = body.count("@FORUPD")
    assert loops >= 4, loops
    # the volatile member is read twice: no forwarding of the store
    assert body.count("ld\tHL,(_st)") >= 2
    assert "ldir" not in body.lower()


@pytest.mark.parametrize("flags", [(), ("-O0",), ("--int=32",),
                                   ALL_OFF], ids=ids)
def test_volatile_loops_run(tmp_path, flags):
    want = host(tmp_path, D_RUN)
    out, _ = uc(tmp_path, D_RUN, *flags)
    assert out == want
