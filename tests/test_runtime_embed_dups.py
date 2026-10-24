"""A runtime function must not be defined twice in one image.

The compiler embeds the runtime functions a program uses.  libc modules ask
the linker for other runtime functions, and the linker brings in a whole
runtime module for each.  When that module also defines a function the
compiler embedded, the image carried the function twice and ul80 printed
"%Mult. Def. Global __MUL32: defined in PROG and in RT_ARI".  Worse, the two
copies of a data word such as __tmp32 are two different words: ldiv() returned
quot = -1 in a program that also used 32-bit arithmetic.

main.py now leaves to the library every embedded function that a runtime
module of the link defines anyway (runtime.leave_to_library).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"

pytestmark = pytest.mark.skipif(
    not (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
         and (LIB_DIR / "libc.lib").exists()
         and (LIB_DIR / "runtime.lib").exists()),
    reason="um80/ul80/cpmemu or the built libraries are missing")


def build(tmp_path, source, *extra):
    """Compile, assemble and link; return (program output, ul80 messages, image)."""
    c_file = tmp_path / "p.c"
    c_file.write_text(source)
    mac, rel, com, sym = (tmp_path / ("p" + e) for e in (".mac", ".rel", ".com", ".sym"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file), "-o", str(mac), *extra],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)], check=True, capture_output=True)
    link = subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                           str(LIB_DIR / "runtime.lib"), "-o", str(com), "-S", str(sym)],
                          capture_output=True, text=True)
    assert link.returncode == 0, link.stdout + link.stderr
    end = 0
    for line in sym.read_text().splitlines():
        addr, name = line.split()
        if name == "__END__":
            end = int(addr, 16) - 0x100
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True, text=True,
                         timeout=60, env=dict(os.environ, PYTHONHASHSEED="0"))
    return run.stdout.replace("\r", ""), link.stdout + link.stderr, end


LDIV_AND_LONG_MATH = r"""
#include <stdio.h>
#include <stdlib.h>
long a = 100000L, b = 7L;
int main(void) {
    ldiv_t d = ldiv(a, b);
    long x = a * b + d.rem;
    long y = a / 3 + (a % 7) + (a << 2) + (a >> 1);
    printf("%ld %ld %ld %ld\n", d.quot, d.rem, x, y);
    return 0;
}
"""

FLOAT_AND_PRINTF = r"""
#include <stdio.h>
float f(float x, float y) { return x * y + x / y - 1.0f; }
int main(void) {
    volatile float a = 3.5f, b = 1.25f;
    printf("%f %d\n", f(a, b), (int)(a * b));
    return 0;
}
"""

SQRT_AND_SHIFT = r"""
#include <stdio.h>
#include <stdlib.h>
static int cmp(const void *p, const void *q) { return *(const int *)p - *(const int *)q; }
int main(void) {
    int v[6] = {5, 3, 9, 1, 7, 2};
    long t = 0;
    int i;
    qsort(v, 6, sizeof v[0], cmp);
    for (i = 0; i < 6; i++) t += (long)v[i] * 1000L;
    printf("%d %d %ld\n", v[0], v[5], t);
    return 0;
}
"""


@pytest.mark.parametrize("name,source,expect", [
    ("ldiv", LDIV_AND_LONG_MATH, "14285 5 700005 483338\n"),
    ("float", FLOAT_AND_PRINTF, None),
    ("qsort", SQRT_AND_SHIFT, "1 9 27000\n"),
])
def test_no_symbol_is_defined_twice(tmp_path, name, source, expect):
    out, link_messages, _ = build(tmp_path, source)
    assert "Mult. Def" not in link_messages, link_messages
    if expect is not None:
        assert out == expect


def test_ldiv_quotient_with_32_bit_arithmetic(tmp_path):
    out, _, _ = build(tmp_path, LDIV_AND_LONG_MATH)
    assert out.split()[0] == "14285"      # was -1: two copies of __tmp32


def test_float_and_printf_image_has_one_copy_of_the_float_code(tmp_path):
    """Embedding next to a linked runtime module used to cost the code twice."""
    _, _, embedded = build(tmp_path, FLOAT_AND_PRINTF)
    _, _, library_only = build(tmp_path, FLOAT_AND_PRINTF, "--no-embed-runtime")
    assert embedded <= library_only + 64, (embedded, library_only)
