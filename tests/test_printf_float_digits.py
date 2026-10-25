"""printf of floats with the rounding float runtime.

The %f printer adds half a unit of the last printed digit and then cuts the
digits off, and the %e printer divides once by a power of ten.  Both
depend on how the runtime rounds.  When __fadd began to round to nearest,
printf("%f", 12.34) printed 12.340001, because 12.34000015 plus 5e-7 is
nearer to the float 12.340001 than to 12.340000.  The %f printer now adds
with rounding toward zero (_prt_addz), and the %e printer no longer adds an
ulp to make up for a truncating runtime.

A float has 24 bits, about 7 decimal digits, so the last digit of a
conversion with more digits can differ from glibc by one.  The sample test
keeps that limit from growing.
"""

import random
import struct

from ._clib import build_and_run, skip_without_tools

pytestmark = skip_without_tools

FORMATS = ["%.0e", "%.2e", "%.4e", "%.5e", "%e", "%.0f", "%.1f", "%.2f",
           "%.4f", "%f", "%g", "%.3g"]
# the largest share of the random values that may differ from glibc
LIMITS = {"%.0e": 0.01, "%.2e": 0.02, "%.4e": 0.05, "%.5e": 0.06, "%e": 0.22,
          "%.0f": 0.04, "%.1f": 0.06, "%.2f": 0.07, "%.4f": 0.25, "%f": 0.45,
          "%g": 0.06, "%.3g": 0.03}


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def test_known_values_print_exactly(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    volatile float a = 12.34f, b = 0.1f, c = 2.675f, d = 1.0f, e = 123456.789f;
    volatile float f = 0.000123f, g = 100.0f, h = 1e20f, i = 5.0e-7f;
    printf("%f %f %f %f %f\n", a, -a, b, c, d);
    printf("%f %e %e %g %g\n", e, f, g, h, i);
    printf("%.0f %.1f %.2f\n", 2.6f, 0.26f, 1.005f);
    return 0;
}
""")
    assert out.split("\n") == [
        "12.340000 -12.340000 0.100000 2.675000 1.000000",
        "123456.789062 1.230000e-04 1.000000e+02 1e+20 5e-07",
        "3 0.3 1.00", ""]


def test_random_values_stay_close_to_glibc(tmp_path):
    rng = random.Random(7)
    vals = []
    for _ in range(400):
        k = rng.random()
        if k < 0.4:
            v = rng.uniform(-1000, 1000)
        elif k < 0.6:
            v = round(rng.uniform(-100, 100), rng.randint(0, 5))
        elif k < 0.8:
            v = rng.uniform(-1, 1) * 10 ** rng.randint(-6, 8)
        else:
            v = rng.randint(-100000, 100000) / rng.choice([1, 2, 4, 8, 10, 100])
        vals.append(f32(v))
    bits = [struct.unpack("<I", struct.pack("<f", v))[0] for v in vals]
    body = "".join('printf("%s|", f);' % fm for fm in FORMATS)
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <string.h>
#include <stdint.h>
static const uint32_t T[] = {%s};
int main(void)
{
    int i;
    for (i = 0; i < sizeof T / sizeof T[0]; i++) {
        float f;
        memcpy(&f, &T[i], 4);
        %s
        printf("\n");
    }
    return 0;
}
""" % (",".join("0x%08xUL" % b for b in bits), body))
    lines = [ln for ln in out.split("\n") if "|" in ln]
    assert len(lines) == len(vals)
    bad = {fm: 0 for fm in FORMATS}
    for v, line in zip(vals, lines):
        for fm, got in zip(FORMATS, line.split("|")):
            if got.strip() != (fm % v).strip():
                bad[fm] += 1
    for fm in FORMATS:
        assert bad[fm] <= LIMITS[fm] * len(vals), (fm, bad)
