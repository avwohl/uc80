"""printf of floats is exact: the same text as Python (and glibc) prints.

The float conversions of printf (%f %F %e %E %g %G) used to do float
arithmetic.  They lost the field width of %5.2f, ignored the '+' and ' '
flags, rounded ties away from zero and were off by one in the last digit.
lc_prt_float.mac now makes the exact decimal digits of the float with
integer arithmetic and rounds them to nearest, ties to even, so the result
is the same as the result of the host for the float value, for every
precision.  The expected text here comes from Python's % operator, which
formats the same exact value.
"""

import math
import random
import struct

from ._clib import build_and_run, skip_without_tools

pytestmark = skip_without_tools


def f32(bits):
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def make_cases(seed, count):
    rng = random.Random(seed)
    cases = []
    for _ in range(count):
        flags = "".join(c for c in "-+ #0" if rng.random() < 0.25)
        width = rng.choice(["", "", "1", "5", "8", "10", "12", "20", "40", "100"])
        prec = rng.choice(["", "", ".0", ".1", ".2", ".3", ".5", ".6", ".9",
                           ".12", ".20", ".40"])
        conv = rng.choice("fFeEgG")
        r = rng.random()
        if r < 0.25:
            bits = rng.getrandbits(31)
            if (bits >> 23) == 255:
                bits ^= 0x00800000
        elif r < 0.4:
            bits = rng.getrandbits(23)              # a subnormal value
        elif r < 0.8:
            bits = (rng.randint(100, 160) << 23) | rng.getrandbits(23)
        else:
            bits = (rng.randint(120, 135) << 23) | (rng.getrandbits(8) << 15)
        if rng.random() < 0.3:
            bits |= 0x80000000
        cases.append((flags + width + prec + conv, bits))
    return cases


def run_cases(tmp_path, cases):
    lines = ["#include <stdio.h>", "#include <stdint.h>",
             "static float fbits(uint32_t u)",
             "{ union { uint32_t u; float f; } x; x.u = u; return x.f; }",
             "int main(void)", "{"]
    for spec, bits in cases:
        lines.append(f'    printf("[%{spec}]\\n", fbits(0x{bits:08x}UL));')
    lines += ["    return 0;", "}"]
    out = build_and_run(tmp_path, "\n".join(lines))
    return out.split("\n")


def test_random_floats_match_python(tmp_path):
    cases = make_cases(1234, 200)
    got = run_cases(tmp_path, cases)
    for i, (spec, bits) in enumerate(cases):
        want = "[" + ("%" + spec) % f32(bits) + "]"
        assert got[i] == want, (spec, hex(bits))


def test_width_flags_and_precision_of_the_common_cases(tmp_path):
    cases = [("5.2f", 0x40490fdb), ("10.3f", 0x40490fdb),
             ("-8.1f", 0x40490fdb), ("08.2f", 0x40490fdb),
             ("+.3e", 0x42f6e979), ("+10.2f", 0xc0490fdb),
             (" f", 0x3fc00000), ("12g", 0x3dcccccd), ("#.3g", 0x40000000),
             ("#.0f", 0x40400000), ("#.0e", 0x40400000), ("20.10e", 0x3f800000)]
    got = run_cases(tmp_path, cases)
    for i, (spec, bits) in enumerate(cases):
        want = "[" + ("%" + spec) % f32(bits) + "]"
        assert got[i] == want, spec


def test_ties_round_to_even_and_carry_into_the_next_digit(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    volatile float a = 0.5f, b = 1.5f, c = 2.5f, d = 0.125f, e = 9.5f;
    volatile float f = 999999.5f, g = 0.9999999f;
    printf("%.0f %.0f %.0f %.2f %.0f\n", a, b, c, d, e);
    printf("%.0e %.0e %.1e\n", c, e, 9.95f);
    printf("%g %g %.2g %.3g\n", f, g, 99.5f, 999.5f);
    return 0;
}
""")
    assert out.split("\n")[:3] == [
        "0 2 2 0.12 10",
        "2e+00 1e+01 9.9e+00",
        "1e+06 1 1e+02 1e+03", ]


def test_infinity_nan_and_negative_zero(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <math.h>
int main(void)
{
    volatile float z = 0.0f;
    float inf = 1.0f / z, nan = z / z;
    printf("[%f][%F][%e][%G][%8.2f][%-8f][%08f][%+f]\n",
           inf, inf, -inf, inf, nan, nan, inf, inf);
    printf("[%f][%g][%e][%+.1f]\n", -z, -z, -z, -z);
    return 0;
}
""")
    assert out.split("\n")[:2] == [
        "[inf][INF][-inf][INF][     nan][nan     ][     inf][+inf]",
        "[-0.000000][-0][-0.000000e+00][-0.0]"]


def test_precision_beyond_the_digits_pads_with_zeros(tmp_path):
    got = run_cases(tmp_path, [(".60f", 0x3f800000), (".60e", 0x00000001),
                               (".100f", 0x00000001), ("#.50g", 0x3dcccccd)])
    for line, (spec, bits) in zip(got, [(".60f", 0x3f800000),
                                        (".60e", 0x00000001),
                                        (".100f", 0x00000001),
                                        ("#.50g", 0x3dcccccd)]):
        assert line == "[" + ("%" + spec) % f32(bits) + "]"


def test_the_float_printer_links_no_float_arithmetic(tmp_path):
    """%f needs no __fadd, __fmul, __fdiv or __ftoi any more."""
    import subprocess
    import sys
    from ._clib import REPO
    src = tmp_path / "p.c"
    src.write_text('#include <stdio.h>\n'
                   'int main(void){ volatile float f = 2.5f;'
                   ' printf("%f %e %g\\n", f, f, f); return 0; }\n')
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(src), "-o",
                        str(tmp_path / "p.mac")], capture_output=True,
                       text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "p.mac").read_text().lower()
    for name in ("__fadd", "__fmul", "__fdiv", "__ftoi", "__itof"):
        assert "call\t" + name not in text, name
