"""A 32-bit int value that the code leaves in HL only (--int 32).

Two cases, both found by ccfuzz with --int=32.  First, a conditional of
two unsigned short operands (below).  Second, a comparison that is cast
to a 64-bit type: ``(long long)(ULL)(a < b)`` took DE from the stack
because the 64-bit extension assumed a 4-byte int is in DEHL, and gave
-65536 for 0 (the fuzz seeds 70211, 70305, 70321 and 70388 of the
--ll mode).  Third, a bit-field of an enumeration type is unsigned: with
--int 32 the enumeration became a signed int before the layout code looked
at the member, and c-testsuite 00218 failed.

A conditional of two unsigned short operands with --int 32.

The operands promote to int, which has 4 bytes with --int 32, so the
conditional has a 32-bit value.  gen_ternary left the branches in HL
only, and the code that read DEHL (a cast to unsigned long, a subtraction
from an unsigned long) used garbage in DE:
``(unsigned long)(c ? (unsigned short)19163 : (unsigned short)gl)`` gave
143477467.  The case was found by ccfuzz with --int=32 (the seeds 60007
and 60010 with the fuzzer variant that types I16 as short).  Expected
values are the host values.
"""

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401

SRC = r'''#include <stdio.h>
long gl = 5; signed char c1 = 1;
unsigned long f(void) { return (unsigned long)(c1 ? (unsigned short)19163 : (unsigned short)gl); }
unsigned long h(unsigned short a, unsigned short b) { return (unsigned long)(c1 ? a : b); }
long g(void) { unsigned short z = 0;
  return (long)((unsigned long)z - (unsigned long)(c1 ? (unsigned short)19163 : (unsigned short)gl)); }
int main(void) {
  printf("%lu %lu %lu %ld\n", f(), h(7, 8), (unsigned long)(c1 ? (unsigned short)65535 : (unsigned short)1), g());
  c1 = 0;
  printf("%lu %lu\n", f(), h(7, 8));
  return 0;
}
'''
WANT = "19163 7 65535 -19163\n5 8\n"


def test_unsigned_short_conditional_is_32_bit_with_int_32(tmp_path):
    assert build_and_run(tmp_path, SRC, flags=("--int", "32")) == WANT


def test_same_program_with_16_bit_int(tmp_path):
    assert build_and_run(tmp_path, SRC) == WANT

CMP = r'''#include <stdio.h>
typedef long long LL; typedef unsigned long long ULL;
short gi = -5; LL gx = 0x100000003LL; ULL ga = 5, gb = 3;
LL f6(LL p0) { return (LL)(ULL)(((ULL)(gi)) < (ULL)(p0)); }
LL f7(ULL a, ULL b) { return (LL)(ULL)(a < b); }
LL f8(ULL a) { return (LL)(ULL)(a < (ULL)(gx++)); }
LL f9(long a, long b) { return (LL)(a == b) + (LL)(a != b) * 2; }
int main(void) {
  printf("%ld %ld %ld %ld %ld\n", (long)f6(504), (long)f7(ga, gb), (long)f7(gb, ga), (long)f8(ga), (long)f9(4, 4));
  printf("%ld\n", (long)f9(1, 2));
  return 0;
}
'''
CMP_WANT = "0 0 1 1 1\n2\n"


def test_comparison_cast_to_long_long_with_int_32(tmp_path):
    assert build_and_run(tmp_path, CMP, flags=("--int", "32")) == CMP_WANT


def test_comparison_cast_to_long_long_with_16_bit_int(tmp_path):
    assert build_and_run(tmp_path, CMP) == CMP_WANT

ENUM_BF = r'''#include <stdio.h>
enum tc { A = 148, B };
struct s { void *p; enum tc code : 8; unsigned f : 1; };
int main(void) {
  struct s x;
  x.code = B; x.f = 1;
  printf("%d %d %d\n", (int)x.code, x.code == B, (int)x.f);
  return 0;
}
'''


def test_enum_bit_field_is_unsigned_with_int_32(tmp_path):
    assert build_and_run(tmp_path, ENUM_BF, flags=("--int", "32")) == "149 1 1\n"


def test_enum_bit_field_is_unsigned_with_16_bit_int(tmp_path):
    assert build_and_run(tmp_path, ENUM_BF) == "149 1 1\n"
