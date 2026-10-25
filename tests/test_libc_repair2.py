"""Float division with infinity, NaN and zero operands; the 0b prefix.

__fdiv lacked the exponent-255 cases (inf/2 was a huge finite number,
inf/inf was 1.0, nan/2 was not a NaN) and the sign of a zero numerator.
strtol, strtoul, strtoll, strtoull and scanf %i must accept 0b / 0B (C23);
scanf %b reads a binary number.
"""
import struct

from ._clib import build_and_run, skip_without_tools

pytestmark = skip_without_tools

DIV = r"""
#include <stdio.h>
static unsigned long bits(float f) { unsigned long u; __builtin_memcpy(&u, &f, 4); return u; }
volatile float vinf, vnan, v2, vm1, v0, vm0;
int main(void) {
    float z;
    vinf = __builtin_inff(); vnan = __builtin_nanf(""); v2 = 2.0f;
    vm1 = -1.0f; v0 = 0.0f; vm0 = -0.0f;
    z = vinf / v2;   printf("%08lx\n", bits(z));
    z = -vinf / v2;  printf("%08lx\n", bits(z));
    z = vinf / vm1;  printf("%08lx\n", bits(z));
    z = vinf / v0;   printf("%08lx\n", bits(z));
    z = v2 / vinf;   printf("%08lx\n", bits(z));
    z = vm1 / vinf;  printf("%08lx\n", bits(z));
    z = v0 / vm1;    printf("%08lx\n", bits(z));
    z = vm0 / v2;    printf("%08lx\n", bits(z));
    z = v0 / -vinf;  printf("%08lx\n", bits(z));
    z = v2 / v0;     printf("%08lx\n", bits(z));
    z = vm1 / v0;    printf("%08lx\n", bits(z));
    z = vm1 / vm0;   printf("%08lx\n", bits(z));
    z = vinf / vinf; printf("nan %d\n", z != z || (bits(z) & 0x7fc00000UL) == 0x7fc00000UL);
    z = vinf / vnan; printf("nan %d\n", (bits(z) & 0x7f800000UL) == 0x7f800000UL && (bits(z) & 0x7fffffUL));
    z = vnan / v2;   printf("nan %d\n", (bits(z) & 0x7f800000UL) == 0x7f800000UL && (bits(z) & 0x7fffffUL));
    z = v2 / vnan;   printf("nan %d\n", (bits(z) & 0x7f800000UL) == 0x7f800000UL && (bits(z) & 0x7fffffUL));
    z = v0 / v0;     printf("nan %d\n", (bits(z) & 0x7f800000UL) == 0x7f800000UL && (bits(z) & 0x7fffffUL));
    z = v2 / vm1;    printf("%08lx\n", bits(z));
    return 0;
}
"""


def test_fdiv_special_operands(tmp_path):
    out = build_and_run(tmp_path, DIV, args=("--printf", "long")).split("\n")
    exp = ["7f800000", "ff800000", "ff800000", "7f800000", "00000000",
           "80000000", "80000000", "80000000", "80000000", "7f800000",
           "ff800000", "7f800000", "nan 1", "nan 1", "nan 1", "nan 1",
           "nan 1", struct.pack(">f", -2.0).hex()]
    assert out[:len(exp)] == exp, out


BIN = r"""
#include <stdio.h>
#include <stdlib.h>
int main(void) {
    char *e; long v; unsigned long u; int a = -1, b = -1, c = -1, n;
    static const char s1[] = "0b101", s2[] = "-0B11", s3[] = "0b111",
        s4[] = "0b", s5[] = "0b2";
    v = strtol(s1, &e, 0); printf("%ld %d\n", v, (int)(e - s1));
    v = strtol(s2, &e, 0); printf("%ld %d\n", v, (int)(e - s2));
    v = strtol(s1, &e, 2); printf("%ld %d\n", v, (int)(e - s1));
    u = strtoul(s3, &e, 0); printf("%lu %d\n", u, (int)(e - s3));
    printf("%ld\n", (long)strtoll("0b1000", &e, 0));
    printf("%lu\n", (unsigned long)strtoull("0b1001", &e, 2));
    v = strtol(s4, &e, 0); printf("%ld %d\n", v, (int)(e - s4));
    v = strtol(s5, &e, 0); printf("%ld %d\n", v, (int)(e - s5));
    printf("%ld\n", strtol("0b1", &e, 16));
    n = sscanf("0b101 0B11 0x1f", "%i %i %i", &a, &b, &c);
    printf("%d %d %d %d\n", n, a, b, c);
    a = b = -1;
    n = sscanf("101 0b11", "%b %b", &a, &b);
    printf("%d %d %d\n", n, a, b);
    a = -1;
    n = sscanf("0bz", "%i%c", &a, (char *)&b);
    printf("%d %d\n", n, a);
    return 0;
}
"""


def test_binary_prefix(tmp_path):
    out = build_and_run(tmp_path, BIN, args=("--printf", "long")).split("\n")
    assert out[:12] == ["5 5", "-3 5", "5 5", "7 5", "8", "9", "0 1", "0 1",
                        "177", "3 5 3 31", "2 5 3", "2 0"], out
