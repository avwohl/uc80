"""math.h functions that take a float apart or round it to an integer.

frexp, ldexp, scalbn, modf, ilogb, logb, nextafter, remquo, the rounding
functions and the lround family.  They work on the bits of the float, so
the results must be exact, the sign of a zero must be right, and the
subnormal numbers must work.  The expected values come from Python.
"""

import math
import random
import struct

import pytest

from ._clib import build_and_run, decode_f32, encode_f32, skip_without_tools

pytestmark = skip_without_tools

SPECIAL = [0x00000000, 0x80000000, 0x3F800000, 0xBF800000, 0x7F800000,
           0xFF800000, 0x7FC00000, 0x7F7FFFFF, 0xFF7FFFFF, 0x00800000,
           0x00000001, 0x80000001, 0x007FFFFF, 0x3F000000, 0xBF000000,
           0x40000000, 0x00400000, 0x80123456, 0x3F7FFFFF, 0x4B000000]


def values():
    rng = random.Random(21)
    out = list(SPECIAL)
    for _ in range(60):
        out.append(rng.getrandbits(32))
    for _ in range(40):
        out.append((rng.getrandbits(1) << 31) | rng.getrandbits(23))  # subnormal
    for v in (0.5, 1.5, 2.5, -0.5, -1.5, -2.5, 0.49999997, 0.50000006,
              8388607.5, 8388608.0, 8388609.0, 16777216.0, 1e10, -1e10,
              0.1, -0.1, 3.7, -3.7, 123456.5, -123456.5, 2147483520.0,
              -2147483520.0, 1e18, 9.0e15):
        out.append(encode_f32(v))
    return out


def table(vals):
    return ",".join("0x%08xUL" % v for v in vals)


PROLOGUE = r"""
#include <stdio.h>
#include <math.h>
#include <string.h>
#include <stdint.h>
static unsigned long bits(float d)
{
    uint32_t u; memcpy(&u, &d, 4); return (unsigned long)u;
}
static float fr(unsigned long v)
{
    uint32_t u = (uint32_t)v; float d; memcpy(&d, &u, 4); return d;
}
"""


def f32(bits):
    return decode_f32(bits)


def same(bits_got, expect):
    """Compare a 32-bit pattern with a Python float, NaN equal to NaN and
    the sign of zero significant."""
    got = f32(bits_got)
    if math.isnan(expect):
        return math.isnan(got)
    return bits_got == encode_f32(expect)


def test_frexp_ldexp_scalbn_modf_logb(tmp_path):
    vals = values()
    out = build_and_run(tmp_path, PROLOGUE + r"""
static const unsigned long T[] = {%s};
int main(void)
{
    int i, k;
    for (i = 0; i < sizeof T / sizeof T[0]; i++) {
        float x = fr(T[i]), ip, fp, m;
        int e = 77;
        m = frexp(x, &e);
        printf("F %%lx %%lx %%d\n", T[i], bits(m), e);
        fp = modf(x, &ip);
        printf("M %%lx %%lx %%lx\n", T[i], bits(fp), bits(ip));
        printf("L %%lx %%d %%lx\n", T[i], ilogb(x), bits(logb(x)));
        for (k = -160; k <= 160; k += 53)
            printf("S %%lx %%d %%lx %%lx %%lx\n", T[i], k, bits(ldexp(x, k)),
                   bits(scalbn(x, k)), bits(scalbnf(x, k)));
    }
    return 0;
}
""" % table(vals))
    bad = []
    for line in out.split("\n"):
        p = line.split()
        if not p:
            continue
        x = f32(int(p[1], 16))
        if p[0] == "F":
            m, e = int(p[2], 16), int(p[3])
            if math.isinf(x) or math.isnan(x):
                ok = e == 0 and (same(m, x))
            else:
                rm, re_ = math.frexp(x)
                ok = e == re_ and same(m, rm)
            if not ok:
                bad.append(("frexp", p))
        elif p[0] == "M":
            fp, ip = int(p[2], 16), int(p[3], 16)
            if math.isnan(x):
                ok = math.isnan(f32(fp)) and math.isnan(f32(ip))
            elif math.isinf(x):
                ok = same(ip, x) and same(fp, math.copysign(0.0, x))
            else:
                t = float(math.trunc(x))
                if t == 0:
                    t = math.copysign(0.0, x)
                f = x - t
                if f == 0:
                    f = math.copysign(0.0, x)
                ok = same(ip, t) and same(fp, f)
            if not ok:
                bad.append(("modf", p))
        elif p[0] == "L":
            il, lb = int(p[2]), int(p[3], 16)
            if math.isnan(x):
                ok = math.isnan(f32(lb)) and il in (-32768, 32767)
            elif math.isinf(x):
                ok = il == 32767 and f32(lb) == math.inf
            elif x == 0:
                ok = il == -32768 and f32(lb) == -math.inf
            else:
                ok = il == math.frexp(x)[1] - 1 and \
                    f32(lb) == float(il)
            if not ok:
                bad.append(("ilogb/logb", p))
        elif p[0] == "S":
            k = int(p[2])
            exp = x if (math.isinf(x) or math.isnan(x) or x == 0) else None
            if exp is None:
                try:
                    exp = math.ldexp(x, k)
                except OverflowError:
                    exp = math.copysign(math.inf, x)
            for g in p[3:]:
                if not same(int(g, 16), exp):
                    bad.append(("ldexp", p, exp))
    assert not bad, bad[:5]


ROUND_FUNCS = ["floor", "ceil", "trunc", "round", "rint", "nearbyint"]


def test_rounding_and_lround_family(tmp_path):
    vals = values()
    out = build_and_run(tmp_path, PROLOGUE + r"""
static const unsigned long T[] = {%s};
int main(void)
{
    int i;
    for (i = 0; i < sizeof T / sizeof T[0]; i++) {
        float x = fr(T[i]);
        printf("%%lx %%lx %%lx %%lx %%lx %%lx %%lx\n", T[i], bits(floor(x)),
               bits(ceil(x)), bits(trunc(x)), bits(round(x)), bits(rint(x)),
               bits(nearbyintf(x)));
        if (x > -2147483000.0f && x < 2147483000.0f)
            printf("L %%lx %%ld %%ld %%ld %%ld\n", T[i], lround(x), lrint(x),
                   (long)llround(x), (long)llrint(x));
        else if (x > -9.0e18f && x < 9.0e18f)
            printf("Q %%lx %%lld %%lld\n", T[i], llround(x), llrint(x));
    }
    return 0;
}
""" % table(vals))
    bad = []
    for line in out.split("\n"):
        p = line.split()
        if not p:
            continue
        if p[0] == "L":
            x = f32(int(p[1], 16))
            if math.isnan(x) or math.isinf(x):
                continue
            exp = [int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1),
                   round(x)]
            got = [int(p[2]), int(p[3]), int(p[4]), int(p[5])]
            if got != [exp[0], exp[1], exp[0], exp[1]]:
                bad.append(("lround", p, exp))
            continue
        if p[0] == "Q":
            x = f32(int(p[1], 16))
            exp = [int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1),
                   round(x)]
            if [int(p[2]), int(p[3])] != exp:
                bad.append(("llround", p, exp))
            continue
        x = f32(int(p[0], 16))
        got = [int(v, 16) for v in p[1:]]
        if math.isnan(x) or math.isinf(x):
            exp = [x] * 6
        else:
            def sgn(r):
                r = float(r)
                return math.copysign(0.0, x) if r == 0 else r
            exp = [sgn(math.floor(x)), sgn(math.ceil(x)), sgn(math.trunc(x)),
                   sgn(math.copysign(math.floor(abs(x) + 0.5), x)),
                   sgn(round(x)), sgn(round(x))]
        for name, g, e in zip(ROUND_FUNCS, got, exp):
            if not same(g, e):
                bad.append((name, p[0], "%08x" % g, e))
    assert not bad, bad[:6]


def test_fmod_remainder_remquo_nextafter(tmp_path):
    rng = random.Random(8)
    vals = [v for v in values() if (v & 0x7F800000) != 0x7F800000][:100]
    pairs = [(a, b) for a in vals[:25] for b in vals[:25]]
    pairs += [(encode_f32(rng.uniform(-1e5, 1e5)),
               encode_f32(rng.uniform(-50, 50))) for _ in range(150)]
    pairs += [(encode_f32(a), encode_f32(b)) for a, b in (
        (5.5, 2), (-5.5, 2), (5.5, -2), (1e30, 3), (3, 1e30), (7, 7),
        (6, 3), (1, 0.1), (100, 0.1), (2.5, 1), (3.5, 1), (0.5, 1),
        (1.5, 1), (2.5, 5), (8388608, 3), (1e-30, 1e-31), (0.75, 1),
        (1.25, 1), (0.5, 1.0))]
    pairs += [(0x7F800000, 0x3F800000), (0x3F800000, 0x7F800000),
              (0x3F800000, 0), (0x7FC00000, 0x3F800000), (0, 0x3F800000)]
    table_ = ",".join("{0x%08xUL,0x%08xUL}" % p for p in pairs)
    out = build_and_run(tmp_path, PROLOGUE + r"""
static const unsigned long T[][2] = {%s};
int main(void)
{
    int i;
    for (i = 0; i < sizeof T / sizeof T[0]; i++) {
        float x = fr(T[i][0]), y = fr(T[i][1]);
        int q = 99;
        float r = remquo(x, y, &q);
        printf("%%lx %%lx %%lx %%lx %%d %%lx %%lx\n", T[i][0], T[i][1],
               bits(fmod(x, y)), bits(r), q, bits(remainder(x, y)),
               bits(nextafter(x, y)));
    }
    return 0;
}
""" % table_)
    bad = []
    for line in out.split("\n"):
        p = line.split()
        if not p:
            continue
        a, b = int(p[0], 16), int(p[1], 16)
        x, y = f32(a), f32(b)
        gm, gr, gq, grr, gn = (int(p[2], 16), int(p[3], 16), int(p[4]),
                               int(p[5], 16), int(p[6], 16))
        if math.isnan(x) or math.isnan(y) or math.isinf(x) or y == 0:
            exp_m = exp_r = math.nan
        elif math.isinf(y):
            exp_m = exp_r = x
        elif x == 0:
            exp_m = exp_r = x
        else:
            exp_m = math.fmod(x, y)
            exp_r = math.remainder(x, y)
        if exp_m == 0 and not math.isnan(exp_m):
            exp_m = math.copysign(0.0, x)
        if exp_r == 0 and not math.isnan(exp_r):
            exp_r = math.copysign(0.0, x)
        if not same(gm, exp_m):
            bad.append(("fmod", x, y, gm, exp_m))
        if not same(gr, exp_r) or not same(grr, exp_r):
            bad.append(("remainder", x, y, "%08x" % gr, exp_r))
        if not (math.isnan(exp_r) or math.isinf(x) or y == 0
                or math.isinf(y) or x == 0):
            n = round((x - exp_r) / y)
            if abs(n) < 1 << 15 and (gq & 7) != (n & 7 if n >= 0 else (-n) & 7) \
                    and abs(gq) & 7 != abs(n) & 7:
                bad.append(("remquo", x, y, gq, n))
            if abs(n) < 1 << 15 and gq != 0 and (gq < 0) != ((x < 0) != (y < 0)):
                bad.append(("remquo sign", x, y, gq, n))
        # nextafter
        if math.isnan(x) or math.isnan(y):
            exp_n = math.nan
        elif x == y:
            exp_n = y
        else:
            exp_n = math.nextafter(x, y)
            # nextafter in Python works on doubles: use float32 steps
            xb = a
            if x == 0:
                exp_n = decode_f32((b & 0x80000000) | 1)
            else:
                up = (y > x) == (x > 0)
                nb = xb + 1 if up else xb - 1
                exp_n = decode_f32(nb)
        if not same(gn, exp_n):
            bad.append(("nextafter", x, y, "%08x" % gn, exp_n))
    assert not bad, bad[:6]


def test_fma_rounds_once(tmp_path):
    from fractions import Fraction
    rng = random.Random(4)
    triples = []
    for _ in range(150):
        e = rng.randint(100, 150)
        x = (rng.getrandbits(1) << 31) | (e << 23) | rng.getrandbits(23)
        y = (rng.getrandbits(1) << 31) | (rng.randint(100, 150) << 23) \
            | rng.getrandbits(23)
        # z close to -x*y so that the sum cancels, or unrelated
        p = decode_f32(x) * decode_f32(y)
        z = encode_f32(-p * (1 + rng.choice([0, 2.0 ** -23, -2.0 ** -22,
                                             2.0 ** -20, 0.5, 1e-9])))
        triples.append((x, y, z if rng.random() < 0.7 else
                        encode_f32(rng.uniform(-1e3, 1e3))))
    triples += [(0x3F800000, 0x3F800000, 0x3F800000),
                (0x40000000, 0x40400000, 0xC0C00000),
                (0x7F800000, 0x3F800000, 0x3F800000),
                (0x7FC00000, 0x3F800000, 0x3F800000),
                (0x3F800000, 0x7F800000, 0xFF800000),
                # the product overflows the float range, the sum is exact
                (0x60AD78EC, 0x60AD78EC, 0xFF800000),
                (0xE0AD78EC, 0x60AD78EC, 0x7F800000),
                (0x60AD78EC, 0x60AD78EC, 0x7F800000)]
    table_ = ",".join("{0x%08xUL,0x%08xUL,0x%08xUL}" % t for t in triples)
    out = build_and_run(tmp_path, PROLOGUE + r"""
static const unsigned long T[][3] = {%s};
int main(void)
{
    int i;
    for (i = 0; i < sizeof T / sizeof T[0]; i++)
        printf("%%lx\n", bits(fma(fr(T[i][0]), fr(T[i][1]), fr(T[i][2]))));
    return 0;
}
""" % table_)
    lines = [ln for ln in out.split("\n") if ln]
    assert len(lines) == len(triples)
    bad = []
    for (a, b, c), line in zip(triples, lines):
        x, y, z = decode_f32(a), decode_f32(b), decode_f32(c)
        got = int(line, 16)
        if any(map(math.isnan, (x, y, z))):
            ok = math.isnan(decode_f32(got))
        elif math.isinf(z) and not math.isinf(x) and not math.isinf(y):
            ok = got == c       # a finite exact product never changes an infinity
        elif any(map(math.isinf, (x, y, z))):
            ok = got in (0x7F800000, 0xFF800000, 0x7FC00000) or \
                same(got, x * y + z if not math.isnan(x * y + z) else math.nan)
        else:
            exact = Fraction(x) * Fraction(y) + Fraction(z)
            exp = encode_f32(float(exact))
            ok = abs(got - exp) <= (1 if exact != 0 else 0) and \
                (exp & 0x7F800000) != 0
            if exact == 0:
                ok = (got & 0x7FFFFFFF) == 0
        if not ok:
            bad.append((x, y, z, "%08x" % got))
    assert not bad, (len(bad), bad[:4])
