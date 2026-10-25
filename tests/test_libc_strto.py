"""strtol, strtoul, strtoll, strtoull, atoll, strtod and strtof.

The expected values come from Python.  Each integer case runs all four
integer functions on one string and one base: the value, the end pointer
and errno.  The float cases compare the 32-bit pattern, so a rounding
error of one unit in the last place fails the test.
"""

import random
import re

import pytest

from ._clib import (build_and_run, decode_f32, encode_f32, skip_without_tools,
                    ulp_distance)

pytestmark = skip_without_tools

WS = " \t\n\v\f\r"


def c_string(s):
    out = []
    for ch in s:
        out.append({"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t",
                    "\v": "\\v", "\f": "\\f", "\r": "\\r"}.get(ch, ch))
    return '"' + "".join(out) + '"'


def int_reference(s, base, bits, signed):
    """(value, end offset, errno) of the C function, for base 0 or 2..36."""
    i, n = 0, len(s)
    while i < n and s[i] in WS:
        i += 1
    neg = False
    if i < n and s[i] in "+-":
        neg = s[i] == "-"
        i += 1
    if (base in (0, 16) and s[i:i + 2] in ("0x", "0X") and i + 2 < n
            and s[i + 2].isalnum() and int(s[i + 2], 36) < 16):
        i += 2
        base = 16
    elif base == 0:
        base = 8 if s[i:i + 1] == "0" else 10
    start, value = i, 0
    while i < n and s[i].isascii() and s[i].isalnum() \
            and int(s[i], 36) < base:
        value = value * base + int(s[i], 36)
        i += 1
    if i == start:
        return (0, 0, 0)
    err = 0
    if signed:
        limit = 1 << (bits - 1)
        if neg:
            if value > limit:
                value, err = limit, 34
            value = -value
        elif value > limit - 1:
            value, err = limit - 1, 34
    elif value >= 1 << bits:
        value, err = (1 << bits) - 1, 34
    elif neg:
        value = (-value) & ((1 << bits) - 1)
    return (value, i, err)


INT_STRINGS = [
    "0", "1", "-1", "+5", "  42", "\t\n 7x", "12abc", "0x1F", "0X1f", "0x",
    "0xg", "0b101", "010", "08", "-0x10", "  -  5", "", "   ", "+", "-", "z",
    "Z", "zz", "35", "ZZ", "1z", "2147483647", "2147483648", "-2147483648",
    "-2147483649", "4294967295", "4294967296", "-4294967295",
    "9223372036854775807", "9223372036854775808", "-9223372036854775808",
    "-9223372036854775809", "18446744073709551615", "18446744073709551616",
    "99999999999999999999", "0777", "0xFFFFFFFF", "0x100000000", "1010101",
    "101", "-101", "12 34", "00000000000000000000005", "0x0", "0X", "0xx1",
    "  +0x1a", "\v5", "\f5", "-0", "0 ", "07 ", "09",
]
INT_BASES = (0, 2, 8, 10, 16, 36, 3, 7)


def int_cases():
    cases = [(s, b) for s in INT_STRINGS for b in INT_BASES]
    rng = random.Random(3)
    alpha = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for _ in range(120):
        b = rng.choice([0, 2, 8, 10, 16, 36, 5, 12, 20, 32])
        pool = alpha[:b] + (alpha[36:36 + b - 10] if b > 10 else "") \
            if b else "0123456789abcdefx"
        s = rng.choice(["", "-", "+", " "]) + "".join(
            rng.choice(pool) for _ in range(rng.randint(1, 22)))
        cases.append((s, b))
    return cases


def test_strtol_family(tmp_path):
    cases = int_cases()
    table = ",\n".join('{%s,%d}' % (c_string(s), b) for s, b in cases)
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
struct C { const char *s; int b; };
static const struct C T[] = {
%s };
int main(void)
{
    int i;
    for (i = 0; i < sizeof T / sizeof T[0]; i++) {
        char *e; long a; unsigned long u; long long q; unsigned long long uq;
        int e1, e2, e3, e4, o1, o2, o3, o4, b = T[i].b;
        errno = 0; a = strtol(T[i].s, &e, b); e1 = errno; o1 = e - T[i].s;
        errno = 0; u = strtoul(T[i].s, &e, b); e2 = errno; o2 = e - T[i].s;
        errno = 0; q = strtoll(T[i].s, &e, b); e3 = errno; o3 = e - T[i].s;
        errno = 0; uq = strtoull(T[i].s, &e, b); e4 = errno; o4 = e - T[i].s;
        printf("%%d %%ld %%d %%d|%%lu %%d %%d|%%lld %%d %%d|%%llu %%d %%d\n",
               i, a, o1, e1, u, o2, e2, q, o3, e3, uq, o4, e4);
    }
    return 0;
}
""" % table)
    lines = [ln for ln in out.split("\n") if "|" in ln]
    assert len(lines) == len(cases)
    bad = []
    for line in lines:
        parts = line.split("|")
        head = parts[0].split()
        idx = int(head[0])
        got = [tuple(int(v) for v in head[1:4])]
        got += [tuple(int(v) for v in p.split()) for p in parts[1:]]
        s, b = cases[idx]
        for k, (bits, signed) in enumerate(
                [(32, True), (32, False), (64, True), (64, False)]):
            exp = int_reference(s, b, bits, signed)
            if b != 0 and not 2 <= b <= 36:
                continue
            if exp[1] == 0 and exp[2] == 0:
                ok = got[k][0] == 0 and got[k][1] == 0
            else:
                ok = exp == got[k]
            if not ok:
                bad.append((k, s, b, exp, got[k]))
    assert not bad, bad[:5]


def test_strtol_bad_base_and_limits(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <limits.h>
int main(void)
{
    char *e; long v;
    errno = 0; v = strtol("99999999999", &e, 10);
    printf("%d %d %d\n", v == LONG_MAX, errno == ERANGE, (int)*e);
    errno = 0; v = strtol("-99999999999", &e, 10);
    printf("%d %d\n", v == LONG_MIN, errno == ERANGE);
    errno = 0; v = strtol("12", &e, 1);
    printf("%ld %d %d\n", v, errno == EINVAL, e != 0);
    errno = 0; v = strtol("12", &e, 37);
    printf("%ld %d\n", v, errno == EINVAL);
    errno = 5; v = strtol("12", &e, 10);
    printf("%ld %d\n", v, errno);
    printf("%lld %lld\n", atoll("-9000000000"), atoll(" 12x"));
    printf("%lld\n", (long long)strtol("zz", 0, 36));
    return 0;
}
""")
    assert out.split("\n") == ["1 1 0", "1 1", "0 1 1", "0 1", "12 5",
                               "-9000000000 12", "1295", ""]


def hexfloat(m, e):
    return m * 2.0 ** e


# (text, expected bits, characters used, errno is ERANGE)
SPECIALS = [
    ("inf", 0x7F800000, 3, 0), ("-inf", 0xFF800000, 4, 0),
    ("INF", 0x7F800000, 3, 0), ("infinity", 0x7F800000, 8, 0),
    ("Infinity xyz", 0x7F800000, 8, 0), ("infinit", 0x7F800000, 3, 0),
    ("nan", 0x7FC00000, 3, 0), ("NaN(abc)x", 0x7FC00000, 8, 0),
    ("nan(", 0x7FC00000, 3, 0), ("-nan", 0xFFC00000, 4, 0),
    ("0x1p3", 0x41000000, 5, 0), ("0X1.8p1", 0x40400000, 7, 0),
    ("0x.8", 0x3F000000, 4, 0), ("0x", 0, 1, 0), ("0xg", 0, 1, 0),
    ("0x1p", 0x3F800000, 3, 0), ("0x1p+", 0x3F800000, 3, 0),
    ("0x1.fffffep127", 0x7F7FFFFF, 14, 0), ("0x1p128", 0x7F800000, 7, 34),
    ("0x1p-149", 1, 8, 0), ("0x1p-150", 0, 8, 34),
    ("0x1.8p-149", 2, 10, 34),
    ("  \t +12.5e1x", 0x42FA0000, 11, 0),
    ("", 0, 0, 0), ("   ", 0, 0, 0), ("+", 0, 0, 0), ("-", 0, 0, 0),
    (".", 0, 0, 0), ("e5", 0, 0, 0), ("0e5", 0, 3, 0),
    ("-0", 0x80000000, 2, 0), ("-0.0e10", 0x80000000, 7, 0),
    ("1e39", 0x7F800000, 4, 34), ("-1e39", 0xFF800000, 5, 34),
    ("1e-46", 0, 5, 34), ("1e-45", 1, 5, 34), ("7e-46", 0, 5, 34),
    ("1.4e-45", 1, 7, 34), ("2e-45", 1, 5, 34),
    ("3.4028235e38", 0x7F7FFFFF, 12, 0), ("3.4028236e38", 0x7F800000, 12, 34),
    ("3.4028234664e38", 0x7F7FFFFF, 15, 0),
    ("16777217", 0x4B800000, 8, 0), ("16777219", 0x4B800002, 8, 0),
    ("000000000000000000000001.5", 0x3FC00000, 26, 0),
    ("1.17549421e-38", 0x007FFFFF, 14, 34),
    ("1e5e5", 0x47C35000, 3, 0), ("1.5.5", 0x3FC00000, 3, 0),
    ("12345678901234567890123", 0x642750AE, 23, 0),
    ("0.1e", 0x3DCCCCCD, 3, 0),
    ("9999999999999999999999e-22", 0x3F800000, 26, 0),
]


def decimal_strings():
    rng = random.Random(7)
    out = ["0", "1", "-1", "0.5", "0.1", "0.2", "0.3", "3.14159265",
           "2.718281828", "1e10", "1e-10", "1e38", "3.4028235e38", "1e-38",
           "1.17549435e-38", "123456789", "99999999999", "0.000001",
           "1.5e+3", "1E5", ".5", "5.", "-.5e1", "0.30000001",
           "1234567890123456789", "0.1234567890123456789", "1e-5",
           "12345.678e-3", "6.02214076e23", "1.602176634e-19"]
    for _ in range(80):
        out.append("%de%d" % (rng.randint(1, 10 ** rng.randint(1, 9)),
                              rng.randint(-30, 30)))
    for _ in range(40):
        out.append("%.*f" % (rng.randint(1, 8), rng.uniform(-1000, 1000)))
    return out


def test_strtod_special_cases(tmp_path):
    table = ",\n".join(c_string(t[0]) for t in SPECIALS)
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <stdint.h>
static const char *T[] = {
%s };
static unsigned long bits(float d)
{
    uint32_t u; memcpy(&u, &d, 4); return (unsigned long)u;
}
int main(void)
{
    int i;
    for (i = 0; i < sizeof T / sizeof T[0]; i++) {
        char *e; float f; double d;
        errno = 0; d = strtod(T[i], &e);
        printf("%%lx %%d %%d ", bits(d), (int)(e - T[i]), errno);
        f = strtof(T[i], &e);
        printf("%%lx %%d\n", bits(f), (int)(e - T[i]));
    }
    return 0;
}
""" % table)
    lines = [ln for ln in out.split("\n") if ln]
    assert len(lines) == len(SPECIALS)
    for (text, bits, used, err), line in zip(SPECIALS, lines):
        b1, n1, e1, b2, n2 = line.split()
        assert (int(b1, 16), int(n1), int(e1)) == (bits, used, err), text
        assert (int(b2, 16), int(n2)) == (bits, used), text


def test_strtod_rounds_correctly(tmp_path):
    texts = decimal_strings()
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
static const char *T[] = {
%s };
int main(void)
{
    int i;
    for (i = 0; i < sizeof T / sizeof T[0]; i++) {
        char *e; float f = strtod(T[i], &e); uint32_t u;
        memcpy(&u, &f, 4);
        printf("%%lx %%d\n", (unsigned long)u, (int)(e - T[i]));
    }
    return 0;
}
""" % ",\n".join(c_string(t) for t in texts))
    lines = [ln for ln in out.split("\n") if ln]
    assert len(lines) == len(texts)
    for text, line in zip(texts, lines):
        bits, used = line.split()
        assert int(used) == len(text), text
        assert int(bits, 16) == encode_f32(float(text)), text
