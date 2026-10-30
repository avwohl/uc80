"""Run-time tests for the decimal conversion and console routines in libc.

_prt_div16 (16 bit divide by 10), _div32_10 (32 bit divide by 10), putchar
and __conout were rewritten for speed.  These tests print numbers and
characters through the real library and compare with Python.
"""

import pytest

from tests._clib import build_and_run, skip_without_tools

pytestmark = skip_without_tools


def test_u16_and_d16_every_value(tmp_path):
    src = r'''
#include <stdio.h>
int main(void) {
    unsigned u = 0;
    do {
        printf("%u %d\n", u, (int)u);
        u++;
    } while (u != 0);
    return 0;
}
'''
    out = build_and_run(tmp_path, src).split("\n")
    want = ["%d %d" % (u, u - 65536 if u >= 32768 else u)
            for u in range(65536)]
    assert out[:65536] == want


VALUES32 = [0, 1, 9, 10, 11, 99, 100, 65535, 65536, 65537, 999999999,
            1000000000, 2**31 - 1, 2**31, 2**31 + 1, 2**32 - 10, 2**32 - 1]


def test_long_values(tmp_path):
    items = ", ".join("%dUL" % v for v in VALUES32)
    src = r'''
#include <stdio.h>
static const unsigned long v[] = { %s };
int main(void) {
    int i;
    for (i = 0; i < (int)(sizeof v / sizeof v[0]); i++)
        printf("%%lu %%ld\n", v[i], (long)v[i]);
    return 0;
}
''' % items
    out = build_and_run(tmp_path, src).split("\n")
    want = ["%d %d" % (v, v - 2**32 if v >= 2**31 else v) for v in VALUES32]
    assert out[:len(want)] == want


def test_long_long_values(tmp_path):
    vals = [0, 9, 10, 2**32, 2**32 - 1, 2**63 - 1, 2**64 - 1,
            12345678901234567890]
    items = ", ".join("%dULL" % v for v in vals)
    src = r'''
#include <stdio.h>
static const unsigned long long v[] = { %s };
int main(void) {
    int i;
    for (i = 0; i < (int)(sizeof v / sizeof v[0]); i++)
        printf("%%llu %%lld\n", v[i], (long long)v[i]);
    return 0;
}
''' % items
    out = build_and_run(tmp_path, src).split("\n")
    want = ["%d %d" % (v, v - 2**64 if v >= 2**63 else v) for v in vals]
    assert out[:len(want)] == want


def test_32bit_dense_around_digit_boundaries(tmp_path):
    src = r'''
#include <stdio.h>
int main(void) {
    unsigned long x = 4294967000UL, s = 0;
    unsigned long n = 0;
    for (n = 0; n < 400; n++, x++) {
        printf("%lu\n", x);
        if (x == 4294967295UL) break;
    }
    for (x = 99990UL; x < 100010UL; x++) printf("%lu\n", x);
    return 0;
}
'''
    out = build_and_run(tmp_path, src).split("\n")
    want = [str(v) for v in range(4294967000, 4294967296)]
    want += [str(v) for v in range(99990, 100010)]
    assert out[:len(want)] == want


CONSOLE_RAW = r'''
#include <stdio.h>
int main(void) {
    int r;
    putchar('a'); putchar('\n');
    putchar('\r'); putchar('\n');
    putchar('\r'); putchar('b'); putchar('\r');
    putchar('\n'); putchar('\n');
    r = putchar('Z');
    putchar('\n');
    printf("r=%d\n", r);
    r = putchar(10);
    printf("r=%d\n", r);
    return 0;
}
'''


def test_console_crlf_rules(tmp_path):
    out = build_and_run(tmp_path, CONSOLE_RAW, raw=True)
    assert out.startswith("a\r\n\r\n\rb\r\n\r\nZ\r\nr=90\r\n\r\nr=10\r\n")


def test_console_no_crlf(tmp_path):
    out = build_and_run(tmp_path, CONSOLE_RAW, args=("--no-crlf",), raw=True)
    assert out.startswith("a\n\r\n\rb\r\n\nZ\nr=90\n\nr=10\n")
