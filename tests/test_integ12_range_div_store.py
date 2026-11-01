"""Range division (family J) and the conversion in a store.

A store to a local of a narrow type wraps the value into the type, so the
range of the stored expression is not the range of the local.  Found by
difftest seed 9574: a plain char that holds 0xC5 (-59) was divided by
2147483648UL as unsigned long (0xFFFFFFC5 / 0x80000000 = 1), and the
pass folded the division to 0.  The expected values are fixed 32-bit
results, so the test needs no host compiler.
"""

import pytest

from .test_famj_kernels import run_raw, ids
from .test_famj_startup import compile_c
from .test_struct_return_abi import _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

SRC = r"""
#include <stdio.h>

signed char g6 = 5;
static unsigned int f28(unsigned int a, unsigned long b) { return 1; }

int main(void)
{
    char c = (f28(0, g6) * (g6 - 0x40));
    signed char sc;
    unsigned char uc;
    unsigned long a, b, d, e;
    sc = (signed char) 65477u;
    uc = 65477u;
    a = c / 2147483648UL;
    b = sc / 2147483648UL;
    d = uc / 2147483648UL;
    e = (unsigned long) uc % 100000UL;
    printf("%lu %lu %lu %lu\n", a, b, d, e);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("--no-range-div",), ("-O0",)],
                         ids=ids)
def test_a_narrow_local_wraps_before_the_division(tmp_path, flags):
    mac = compile_c(tmp_path, SRC, *flags)
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out == "1 1 0 197\n"


# ---------------------------------------------------------------------------
# Repair of integrate12: a plain char that holds 128..255 (and the other
# narrow types) must wrap too.  uc80 has a signed plain char, so
# ``char c = 200`` holds -56.  The first fix clamped a plain char to
# -128..255, kept 200, and folded 200 / 65536UL to 0 where 65535 is right.

def wrap(v, bits, signed):
    v &= (1 << bits) - 1
    return v - (1 << bits) if signed and v >> (bits - 1) else v


# (declaration, statements, bits, signed, value stored as a Python int)
NARROW = [
    ("char c = 200", "", 8, True, 200),
    ("char c = 255", "", 8, True, 255),
    ("char c = 128", "", 8, True, 128),
    ("signed char c = 200", "", 8, True, 200),
    ("unsigned char c = 300", "", 8, False, 300),
    ("short c = 40000", "", 16, True, 40000),
    ("unsigned short c = 70000", "", 16, False, 70000),
    ("char c", "c = 200;", 8, True, 200),
    ("char c", "c = 255;", 8, True, 255),
    ("char c", "c = 128;", 8, True, 128),
    ("char c", "c = 127;", 8, True, 127),
    ("char c", "c = -59;", 8, True, -59),
    ("char c = 100", "c += 100;", 8, True, 200),
    ("char c = 100", "c *= 3;", 8, True, 300),
    ("char c = 120", "c++; c++; c++; c++; c++; c++; c++; c++; c++;",
     8, True, 129),
    ("signed char c", "c = 200;", 8, True, 200),
    ("unsigned char c", "c = 300;", 8, False, 300),
    ("unsigned char c = 250", "c++; c++; c++; c++; c++; c++; c++; c++;",
     8, False, 258),
    ("short c", "c = 40000;", 16, True, 40000),
    ("short c = 30000", "c += 5000;", 16, True, 35000),
    ("unsigned short c", "c = 70000;", 16, False, 70000),
]
DIVISORS = [("256u", 16), ("1000u", 16), ("65536UL", 32), ("100000UL", 32),
            ("2147483648UL", 32)]


def narrow_source():
    lines = ["#include <stdio.h>", "unsigned long r;"]
    for i, (decl, stmts, _b, _s, _v) in enumerate(NARROW):
        lines.append(f"void f{i}(void)")
        lines.append("{")
        lines.append(f"    {decl};")
        if stmts:
            lines.append(f"    {stmts}")
        lines.append('    printf("%ld", (long) c);')
        for dv, _w in DIVISORS:
            lines.append(f'    r = c / {dv}; printf(" %lu", r);')
        lines.append('    printf("\\n");')
        lines.append("}")
    lines.append("int main(void)\n{")
    for i in range(len(NARROW)):
        lines.append(f"    f{i}();")
    lines.append("    return 0;\n}")
    return "\n".join(lines) + "\n"


def narrow_expected():
    out = []
    for _decl, _st, bits, signed, v in NARROW:
        w = wrap(v, bits, signed)
        row = [str(w)]
        for dv, dbits in DIVISORS:
            div = int("".join(ch for ch in dv if ch.isdigit()))
            mask = (1 << dbits) - 1
            row.append(str((w & mask) // div))
        out.append(" ".join(row))
    return "\n".join(out) + "\n"


@pytest.mark.parametrize("flags", [(), ("--no-range-div",), ("-O0",)],
                         ids=ids)
def test_a_narrow_local_holding_a_wrapped_constant(tmp_path, flags):
    mac = compile_c(tmp_path, narrow_source(), *flags)
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out == narrow_expected()
