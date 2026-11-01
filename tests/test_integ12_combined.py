"""Interactions found when family J met bug rounds 3 to 5 (branch integrate12).

The programs run under cpmemu.  The expected output is fixed text, so the
host compiler does not matter.
"""

import pytest

from .test_famj_kernels import ids
from .test_famj_startup import compile_c, run_raw
from .test_struct_return_abi import _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

FLAGS = [(), ("--no-void-putchar",), ("--int=32",), ("-O0",),
         ("--no-fast-bss-clear",), ("--no-skip-auto-clear",)]

STDIO = r"""
#include <stdio.h>

struct pt { int x, y; };

static void out(const char *s)
{
    while (*s) putchar(*s++);
}

int main(void)
{
    FILE *f = stdout;
    int i;
    putchar('a');
    putchar('\n');
    out("hi\n");
    fputs("fp\n", f);
    fprintf(f, "%d %s\n", 42, "x");
    printf("%u %c|%5.2f\n", 65000u, 'z', 1.5);
    for (i = 0; i < 3; i++) putchar('0' + i);
    putchar('\n');
    printf("%zu\n", sizeof(struct pt));
    return 0;
}
"""

STDIO_OUT = "a\nhi\nfp\n42 x\n65000 z| 1.50\n012\n4\n"


@pytest.mark.parametrize("flags", FLAGS, ids=ids)
def test_putchar_statements_printf_and_file(tmp_path, flags):
    mac = compile_c(tmp_path, STDIO, *flags)
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    want = STDIO_OUT
    if "--int=32" in flags:
        want = want.replace("\n4\n", "\n8\n")
    assert out == want


UP = r"""
#include <stdio.h>
static int count;
int putchar(int c) { count++; return c; }
int main(void)
{
    putchar('x'); putchar('y');
    printf("%d\n", count);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("--no-void-putchar",), ("-O0",)],
                         ids=ids)
def test_a_user_putchar_in_c_is_called(tmp_path, flags):
    mac = compile_c(tmp_path, UP, *flags)
    assert "__putchar_v" not in mac.read_text()
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out.strip().endswith("2") or out.strip() == "2"


ENUMS = r"""
#include <stdio.h>
enum small { S0 = 0, S1 = 200, S2 = 255 };
enum neg { N0 = -100, N1 = 100 };
enum big { B0 = 0, B1 = 70000 };
static unsigned char cnt(void) { static unsigned char c = 250; return c++; }
int main(void)
{
    enum small s = S2;
    enum neg n = N0;
    enum big b = B1;
    unsigned char u, k;
    short sh = -3000;
    long t;
    int r = 0;
    for (u = 0; u < 200; u++) r += (s / 300) + (u % 256 == u);
    for (k = 250; k != 4; k++) r += (k > 100) ? 1 : 2;
    t = sh;
    printf("%d %d %ld %ld %d\n", r, n / 1000, (long) b / 100000L,
           t % 100000L, (int) (sh / 4000));
    printf("%d %d\n", (int) (n % 300), (int) cnt());
    return 0;
}
"""
# r = 0 (s/300 is 0, u%256==u is 1: 200) + loop of k from 250 to 3: 10 values;
# k > 100 for 250..255 (6 values) then 0..3 (4 values, 2 each)
# 200 + 6 + 8 = 214;  n/1000 = 0;  70000/100000 = 0;  -3000;  0
# n % 300 = -100;  cnt() = 250


@pytest.mark.parametrize("flags", [(), ("--no-range-div",),
                                   ("--no-range-byte-cmp",), ("-O0",),
                                   ("--int=32",)], ids=ids)
def test_enums_short_and_byte_counters(tmp_path, flags):
    mac = compile_c(tmp_path, ENUMS, *flags)
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out == "214 0 0 -3000 0\n-100 250\n"
