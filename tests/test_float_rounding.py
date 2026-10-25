"""Float + - * / round to nearest, ties to even, like IEEE single precision.

The runtime used to truncate: __fadd lost the bit it shifted out, and
__fdiv produced only 23 bits when the dividend was smaller than the
divisor, so a result could be one or two units in the last place off, and
x - y was not exact for y/2 <= x <= 2y.  The operands here cover equal and
different exponents, cancellation, carries, ties and powers of two.  The
expected values are Python float32 results (a double sum, product or
quotient of two floats rounds to the same float).
"""

import random
import struct

from ._clib import build_and_run, decode_f32, encode_f32, skip_without_tools

pytestmark = skip_without_tools


def operand_pairs():
    rng = random.Random(11)
    pairs = []

    def rnd_mant():
        pick = rng.random()
        if pick < 0.1:
            return 0x7FFFFF
        if pick < 0.2:
            return 0
        if pick < 0.3:
            return 1 << rng.randint(0, 22)
        return rng.getrandbits(23)

    def make(exp, sign=None):
        s = rng.getrandbits(1) if sign is None else sign
        return (s << 31) | (exp << 23) | rnd_mant()

    for _ in range(700):
        e1 = rng.randint(90, 160)
        kind = rng.random()
        if kind < 0.35:
            e2 = e1 + rng.randint(-2, 2)        # cancellation, carries
        elif kind < 0.7:
            e2 = e1 + rng.randint(-40, 40)      # the alignment shift
        else:
            e2 = rng.randint(90, 160)
        pairs.append((make(e1), make(e2)))
    # a - a, a - (a + ulp), 1 +- tiny, ties
    for e in (100, 127, 130):
        base = (e << 23) | 0x123456
        pairs.append((base, base | 0x80000000))
        pairs.append((base, (base + 1) | 0x80000000))
        pairs.append((base + 1, base | 0x80000000))
    one = 0x3F800000
    for d in range(20, 34):
        pairs.append((one, (127 - d) << 23))
        pairs.append((one, ((127 - d) << 23) | 0x80000000))
        pairs.append((one + 1, (127 - d) << 23))
        pairs.append((one + 1, ((127 - d) << 23) | 0x7FFFFF))
    pairs.append((0x4B800000, 0x3F800000))      # 2^24 + 1 is a tie
    pairs.append((0x4B800001, 0x3F800000))
    pairs.append((0x3F800000, 0x40000000))
    pairs.append((0x3F800000, 0x40400000))      # 1 / 3
    pairs.append((0x40400000, 0x3F800000))
    return pairs


def expected(a, b):
    x, y = decode_f32(a), decode_f32(b)
    out = [encode_f32(x + y), encode_f32(x - y), encode_f32(x * y)]
    out.append(encode_f32(x / y) if y != 0 else None)

    def flush(bits):
        # uc80 has no subnormal numbers: such a result is a signed zero
        if bits is not None and (bits & 0x7F800000) == 0:
            return bits & 0x80000000
        return bits
    return [flush(v) for v in out]


def test_float_basic_ops_are_correctly_rounded(tmp_path):
    pairs = [p for p in operand_pairs()
             if decode_f32(p[0]) != 0 and decode_f32(p[1]) != 0]
    table = ",\n".join("{0x%08xUL, 0x%08xUL}" % p for p in pairs)
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <string.h>
#include <stdint.h>
static const uint32_t T[][2] = {
%s };
static unsigned long bits(float d)
{
    uint32_t u; memcpy(&u, &d, 4); return (unsigned long)u;
}
int main(void)
{
    int i;
    for (i = 0; i < sizeof T / sizeof T[0]; i++) {
        float x, y;
        memcpy(&x, &T[i][0], 4); memcpy(&y, &T[i][1], 4);
        printf("%%lx %%lx %%lx %%lx\n", bits(x + y), bits(x - y),
               bits(x * y), bits(x / y));
    }
    return 0;
}
""" % table)
    lines = [ln for ln in out.split("\n") if ln]
    assert len(lines) == len(pairs)
    bad = []
    for (a, b), line in zip(pairs, lines):
        got = [int(v, 16) for v in line.split()]
        exp = expected(a, b)
        for name, g, e in zip("+-*/", got, exp):
            if g != e:
                bad.append((name, "%08x" % a, "%08x" % b, "%08x" % g,
                            "%08x" % e))
    assert not bad, (len(bad), bad[:6])


def test_float_infinity_and_zero_operands(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <string.h>
#include <stdint.h>
static unsigned long bits(float d)
{
    uint32_t u; memcpy(&u, &d, 4); return (unsigned long)u;
}
static float make(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
int main(void)
{
    volatile float inf = make(0x7F800000UL), ninf = make(0xFF800000UL);
    volatile float nan = make(0x7FC00000UL), one = 1.0f, zero = 0.0f;
    volatile float v = 1.0f;
    volatile float huge = make(0x7F7FFFFFUL);
    printf("%lx %lx %lx\n", bits(inf + one), bits(one + ninf),
           bits(inf + ninf) & 0x7FC00000UL);
    printf("%lx %lx\n", bits(nan + one), bits(one - nan) & 0x7FC00000UL);
    printf("%lx %lx %lx\n", bits(zero + one), bits(one + zero),
           bits(zero + zero));
    printf("%lx %lx\n", bits(huge + huge), bits(-huge - huge));
    printf("%lx %lx %lx\n", bits(one / zero), bits(-one / zero),
           bits(zero / zero) & 0x7FC00000UL);
    printf("%lx\n", bits(v - v));
    return 0;
}
""")
    assert out.split("\n") == [
        "7f800000 ff800000 7fc00000", "7fc00000 7fc00000",
        "3f800000 3f800000 0", "7f800000 ff800000",
        "7f800000 ff800000 7fc00000", "0", ""]


def test_int_to_float_rounds_to_nearest_even(tmp_path):
    rng = random.Random(5)
    values = [0, 1, 16777216, 16777217, 16777219, 2147483647,
              2147483648, 4294967295, 33554431, 33554433]
    for _ in range(150):
        values.append(rng.getrandbits(32) >> rng.randint(0, 31))
    table = ",\n".join("%dUL" % (v & 0xFFFFFFFF) for v in values)
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <string.h>
#include <stdint.h>
static const unsigned long T[] = {
%s };
static unsigned long bits(float d)
{
    uint32_t u; memcpy(&u, &d, 4); return (unsigned long)u;
}
int main(void)
{
    int i;
    for (i = 0; i < sizeof T / sizeof T[0]; i++) {
        volatile unsigned long u = T[i];
        volatile long s = (long)T[i];
        printf("%%lx %%lx\n", bits((float)u), bits((float)s));
    }
    return 0;
}
""" % table)
    lines = [ln for ln in out.split("\n") if ln]
    assert len(lines) == len(values)
    for v, line in zip(values, lines):
        u, s = line.split()
        signed = v - (1 << 32) if v >= 1 << 31 else v
        assert int(u, 16) == encode_f32(float(v & 0xFFFFFFFF)), v
        assert int(s, 16) == encode_f32(float(signed)), v
