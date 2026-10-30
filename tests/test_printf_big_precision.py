"""printf with a float precision over 255 (docs/printf.md).

The engine makes at most 255 digits.  The exact decimal form of a float has
at most 189 digits after the point, so the rest of a larger precision is
zeros.  The expected text comes from Python, which rounds exactly (as glibc
does)."""

import struct

import pytest

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


CASES = [
    ("%.300f", "1.5f", "%.300f" % f32(1.5)),
    ("%.256f", "0.1f", "%.256f" % f32(0.1)),
    ("%.255f", "0.1f", "%.255f" % f32(0.1)),
    ("%.300e", "0.1f", "%.300e" % f32(0.1)),
    ("%.300g", "0.1f", "%.300g" % f32(0.1)),
    ("%#.300g", "0.1f", "%#.300g" % f32(0.1)),
    ("%#.260g", "123456.0f", "%#.260g" % f32(123456.0)),
    ("%10.260f", "0.25f", "%10.260f" % f32(0.25)),
    ("%+.1000f", "-3.0f", "%+.1000f" % f32(-3.0)),
    ("%.4000e", "1e-30f", "%.4000e" % f32(1e-30)),
    ("%300.2f", "2.5f", "%300.2f" % f32(2.5)),
    ("%-300.1f", "7.25f", "%-300.1f" % f32(7.25)),
    ("%0400.300f", "9.75f", "%0400.300f" % f32(9.75)),
    ("%.299f", "3.0e-45f", "%.299f" % f32(3.0e-45)),
    ("%#.256e", "1.0f", "%#.256e" % f32(1.0)),
    ("%.0f", "2.5f", "%.0f" % f32(2.5)),
    ("%.255f", "1.0e38f", "%.255f" % f32(1.0e38)),
]


def test_a_precision_over_255(tmp_path):
    body = "".join(f'    printf("[{fmt}]\\n", {arg});\n'
                   for fmt, arg, _want in CASES)
    out = build_and_run(tmp_path, '#include <stdio.h>\nint main(void) {\n'
                        + body + "    return 0;\n}\n")
    got = out.split("\n")
    assert got[-1] == ""
    for (fmt, arg, want), line in zip(CASES, got):
        assert line == "[" + want + "]", fmt
    assert len(got) - 1 == len(CASES)


@pytest.mark.parametrize("flags", [("--int=32",)])
def test_a_star_precision_over_255(tmp_path, flags):
    out = build_and_run(tmp_path, r'''#include <stdio.h>
int main(void) {
    printf("[%.*f]\n", 300, 0.5f);
    return 0;
}
''', flags)
    assert out == "[" + "%.300f" % 0.5 + "]\n"


def test_a_hex_float_precision_over_255(tmp_path):
    out = build_and_run(tmp_path, r'''#include <stdio.h>
int main(void) {
    printf("[%.300a]\n", 1.5f);
    printf("[%.10a]\n", 0.1f);
    printf("[%.256a]\n", 0.1f);
    printf("[%30.300a]\n", 1.0f);
    return 0;
}
''')
    assert out == ("[0x1.8" + "0" * 299 + "p+0]\n"
                   "[0x1.99999a0000p-4]\n"
                   "[0x1.99999a" + "0" * 250 + "p-4]\n"
                   "[0x1." + "0" * 300 + "p+0]\n")
