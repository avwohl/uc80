"""math.h functions against Python's math module.

Each group is one program that evaluates a few functions on a few hundred
arguments: special values (zero, infinity, NaN, FLT_MAX, the smallest
normal number), points near the edges of the range reduction and random
points.  A result must be within a few units in the last place of the
exact value rounded to a float, and special cases must be exact.  The
float arithmetic of uc80 flushes subnormal results to zero, so a result
below the smallest normal number only has to be that small.

The tolerance in ulps is the largest error that a function has now plus a
margin of one.
"""

import math
import random
import struct

import pytest

from ._clib import (build_and_run, decode_f32, encode_f32, skip_without_tools,
                    ulp_distance)

pytestmark = skip_without_tools

FLT_MIN = 1.1754943508222875e-38
SPECIAL = [0x00000000, 0x80000000, 0x3F800000, 0xBF800000, 0x7F800000,
           0xFF800000, 0x7FC00000, 0x7F7FFFFF, 0xFF7FFFFF, 0x00800000,
           0x00000001, 0x007FFFFF, 0x3F000000, 0xBF000000, 0x40000000,
           0xC0000000]


class Gen:
    def __init__(self, seed):
        self.rng = random.Random(seed)

    def log_uniform(self, lo, hi, n, signed=False):
        out = []
        for _ in range(n):
            v = math.exp(self.rng.uniform(math.log(lo), math.log(hi)))
            if signed and self.rng.random() < 0.5:
                v = -v
            out.append(encode_f32(v))
        return out

    def uniform(self, lo, hi, n):
        return [encode_f32(self.rng.uniform(lo, hi)) for _ in range(n)]


def one_arg_inputs(name):
    g = Gen(sum(map(ord, name)))
    s = list(SPECIAL)
    f = encode_f32
    if name in ("sin", "cos", "tan"):
        return (s + g.uniform(-4, 4, 40) + g.log_uniform(1e-6, 100, 40, True)
                + g.log_uniform(100, 1e5, 25, True)
                + g.log_uniform(1e5, 3e38, 25, True)
                + [f(k * math.pi / 2) for k in range(-12, 13)]
                + [f(x) for x in (5999.9, 6000.1, 4000.0, 100000.0)])
    if name in ("exp", "exp2", "expm1", "sinh", "cosh", "tanh"):
        return (s + g.uniform(-1, 1, 30) + g.uniform(-12, 12, 30)
                + g.uniform(-90, 90, 40) + g.log_uniform(1e-6, 1, 15, True)
                + [f(x) for x in (88.7, 88.72, 88.73, -87.3, -103.9, -104.0,
                                  127.9, 128.1, -125.9, -150.0, 17.3, -17.5)])
    if name in ("log", "log2", "log10", "sqrt", "cbrt"):
        return (s + g.log_uniform(1e-37, 1e37, 80) + g.uniform(0.5, 2, 40)
                + [f(1 - 2.0 ** -24), f(1 + 2.0 ** -23), f(1 - 2.0 ** -23)]
                + [f(float(v)) for v in (2, 4, 8, 10, 100, 1000, 27, 64, 125,
                                         1e6, 0.001)]
                + [f(-1.0), f(-3.5)])
    if name == "log1p":
        return (s + g.uniform(-0.9999, 1, 40) + g.log_uniform(1e-30, 1e30, 40)
                + [f(-1.0), f(-2.0)])
    if name in ("asin", "acos"):
        return (s + g.uniform(-1, 1, 80) + g.log_uniform(1e-6, 1, 15, True)
                + [f(1.5), f(-1.01)])
    if name == "atan":
        return (s + g.uniform(-5, 5, 40) + g.log_uniform(1e-6, 1e8, 40, True)
                + g.log_uniform(1e8, 3e38, 8, True))
    if name == "asinh":
        return s + g.uniform(-3, 3, 40) + g.log_uniform(1e-6, 1e30, 40, True)
    if name == "acosh":
        return (s + g.uniform(1, 5, 40) + g.log_uniform(1, 1e30, 40)
                + [f(0.5)])
    if name == "atanh":
        return (s + g.uniform(-1, 1, 60) + g.log_uniform(1e-6, 1, 15, True)
                + [f(1.5)])
    raise KeyError(name)


def two_arg_inputs(name):
    g = Gen(sum(map(ord, name)))
    s = SPECIAL[:12]
    pairs = [(a, b) for a in s for b in s]
    f = encode_f32
    if name == "pow":
        pairs += [(f(a), f(b)) for a, b in (
            (2, 10), (3, 2), (10, -2), (0.5, 3), (2, 0.5), (-2, 3), (-2, 2),
            (-8, 1 / 3.0), (10, 30), (10, -30), (2, -149), (2, 127), (2, 128),
            (1.0000001, 1e7), (0.9999999, 1e7), (5, 3), (7, 8), (3, 20),
            (1e-3, 2.5), (123.456, 0.789), (0.0, -1), (0.0, 3), (-0.0, 3),
            (-0.0, -3), (0.0, 0.5), (1e30, 2), (1e-30, 2), (2.5, 3.5),
            (9, 0.5), (100, 0.1), (-3, 5), (-3, -5), (2, -1), (4, -0.5),
            (-1, 3e38), (-1, 1e30), (1.5, 1e30), (0.5, 1e30))]
        pairs += list(zip(g.log_uniform(1e-3, 1e3, 60), g.uniform(-8, 8, 60)))
        pairs += list(zip(g.log_uniform(1e-30, 1e30, 25),
                          g.uniform(-3, 3, 25)))
        pairs += [(f(float(g.rng.randint(2, 12))),
                   f(float(g.rng.randint(-12, 20)))) for _ in range(30)]
    elif name == "atan2":
        pairs += list(zip(g.log_uniform(1e-6, 1e6, 50, True),
                          g.log_uniform(1e-6, 1e6, 50, True)))
        pairs += [(f(a), f(b)) for a in (1, -1, 0.0, -0.0, 5, 2)
                  for b in (1, -1, 0.0, -0.0, -2, 3)]
    elif name == "hypot":
        pairs += list(zip(g.log_uniform(1e-20, 1e20, 50, True),
                          g.log_uniform(1e-20, 1e20, 50, True)))
        pairs += [(f(3), f(4)), (f(1e30), f(1e30)), (f(1e-30), f(1e-30))]
    elif name in ("fmin", "fmax", "fdim", "copysign"):
        pairs += list(zip(g.uniform(-10, 10, 30), g.uniform(-10, 10, 30)))
    return pairs


def ref1(name, x):
    if math.isnan(x):
        return math.nan
    try:
        table = {
            "sin": math.sin, "cos": math.cos, "tan": math.tan,
            "exp": math.exp, "expm1": math.expm1,
            "exp2": lambda v: 2.0 ** v if abs(v) < 1100 else
            (math.inf if v > 0 else 0.0),
            "log": lambda v: -math.inf if v == 0 else math.log(v),
            "log2": lambda v: -math.inf if v == 0 else math.log2(v),
            "log10": lambda v: -math.inf if v == 0 else math.log10(v),
            "log1p": lambda v: -math.inf if v == -1 else math.log1p(v),
            "sqrt": lambda v: math.sqrt(v) if v != 0 else v,
            "cbrt": math.cbrt, "asin": math.asin, "acos": math.acos,
            "atan": math.atan, "sinh": math.sinh, "cosh": math.cosh,
            "tanh": math.tanh, "asinh": math.asinh, "acosh": math.acosh,
            "atanh": lambda v: math.atanh(v) if abs(v) != 1
            else math.copysign(math.inf, v),
        }
        return table[name](x)
    except OverflowError:
        return math.copysign(math.inf, x) if name == "sinh" else math.inf
    except ValueError:
        return math.nan


def ref2(name, x, y):
    if name == "pow":
        if y == 0 or x == 1:
            return 1.0
    if name in ("fmin", "fmax") and (math.isnan(x) or math.isnan(y)):
        return y if math.isnan(x) else x
    if name == "copysign":
        return math.copysign(x, y)
    if name == "hypot" and (math.isinf(x) or math.isinf(y)):
        return math.inf
    if math.isnan(x) or math.isnan(y):
        return math.nan
    try:
        if name == "pow":
            return math.pow(x, y)
        if name == "atan2":
            return math.atan2(x, y)
        if name == "hypot":
            return math.hypot(x, y)
        if name == "fmin":
            return min(x, y) if not (x == 0 and y == 0) else \
                (x if math.copysign(1, x) < 0 else y)
        if name == "fmax":
            return max(x, y) if not (x == 0 and y == 0) else \
                (x if math.copysign(1, x) > 0 else y)
        if name == "fdim":
            return max(x - y, 0.0) if not (math.isinf(x) and x == y) else 0.0
    except (OverflowError, ZeroDivisionError):
        return math.inf
    except ValueError:
        if name == "pow" and x == 0 and y < 0:
            odd = y == int(y) and int(y) % 2 == 1
            return -math.inf if math.copysign(1, x) < 0 and odd else math.inf
        return math.nan
    raise KeyError(name)


# the largest error in ulps that each function has, plus one
TOLERANCE = dict(
    sin=3, cos=2, tan=3, exp=2, exp2=2, expm1=3, log=2, log2=3, log10=2,
    log1p=2, sqrt=0, cbrt=0, asin=2, acos=2, atan=2, sinh=2, cosh=2, tanh=3,
    asinh=3, acosh=3, atanh=3, pow=1, atan2=2, hypot=0, fmin=0, fmax=0,
    fdim=0, copysign=0)

ONE = ["sin", "cos", "tan", "exp", "exp2", "expm1", "log", "log2", "log10",
       "log1p", "sqrt", "cbrt", "asin", "acos", "atan", "sinh", "cosh",
       "tanh", "asinh", "acosh", "atanh"]
TWO = ["pow", "atan2", "hypot", "fmin", "fmax", "fdim", "copysign"]
GROUPS = [ONE[0:4], ONE[4:8], ONE[8:12], ONE[12:16], ONE[16:21],
          ["pow", "atan2"], ["hypot", "fmin", "fmax", "fdim", "copysign"]]


def c_program(names):
    lines = ["#include <stdio.h>", "#include <math.h>", "#include <string.h>",
             "#include <stdint.h>",
             "static unsigned long bits(float d)",
             "{ uint32_t u; memcpy(&u, &d, 4); return (unsigned long)u; }",
             "static float fr(unsigned long v)",
             "{ uint32_t u = (uint32_t)v; float d; memcpy(&d, &u, 4); "
             "return d; }"]
    body = ["int main(void) { int i;"]
    for n in names:
        if n in ONE:
            d = one_arg_inputs(n)
            lines.append("static const unsigned long in_%s[] = {%s};"
                         % (n, ",".join("0x%08xUL" % v for v in d)))
            body.append('for (i = 0; i < %d; i++) { float x = fr(in_%s[i]); '
                        'printf("%s %%lx %%lx\\n", in_%s[i], bits(%s(x))); }'
                        % (len(d), n, n, n, n))
        else:
            d = two_arg_inputs(n)
            lines.append("static const unsigned long in_%s[][2] = {%s};"
                         % (n, ",".join("{0x%08xUL,0x%08xUL}" % p for p in d)))
            body.append('for (i = 0; i < %d; i++) { float x = fr(in_%s[i][0]), '
                        'y = fr(in_%s[i][1]); printf("%s %%lx %%lx %%lx\\n", '
                        'in_%s[i][0], in_%s[i][1], bits(%s(x, y))); }'
                        % (len(d), n, n, n, n, n, n))
    body.append("return 0; }")
    return "\n".join(lines + body) + "\n"


def judge(name, args, got_bits, ref):
    """Return None if GOT_BITS is acceptable, else a description."""
    got = decode_f32(got_bits)
    if math.isnan(ref):
        return None if math.isnan(got) else "expected NaN"
    if math.isinf(ref):
        return None if got == ref else "expected %r" % ref
    if abs(ref) < FLT_MIN * 1.0000001:
        return None if abs(got) <= FLT_MIN * 1.0000001 else "expected ~0"
    if math.isinf(got) and abs(ref) < 3.4028235e38:
        return "overflow"
    u = ulp_distance(got_bits, encode_f32(ref))
    if u > TOLERANCE[name]:
        return "%d ulp (limit %d), exact %r" % (u, TOLERANCE[name], ref)
    return None


@pytest.mark.parametrize("names", GROUPS, ids=lambda g: "-".join(g))
def test_math_functions_match_python(tmp_path, names):
    out = build_and_run(tmp_path, c_program(names))
    bad = []
    seen = set()
    for line in out.split("\n"):
        p = line.split()
        if not p:
            continue
        name = p[0]
        seen.add(name)
        if name in ONE:
            x = decode_f32(int(p[1], 16))
            why = judge(name, (x,), int(p[2], 16), ref1(name, x))
            args = (x,)
        else:
            x, y = decode_f32(int(p[1], 16)), decode_f32(int(p[2], 16))
            why = judge(name, (x, y), int(p[3], 16), ref2(name, x, y))
            args = (x, y)
        if why:
            bad.append((name, args, decode_f32(int(p[-1], 16)), why))
    assert seen == set(names)
    assert not bad, (len(bad), bad[:8])
