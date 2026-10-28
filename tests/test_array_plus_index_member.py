"""``(gs + i)->m``: the sum of an array and an integer is a pointer.

The type of ``gs + i`` was the array type of ``gs``, so the arrow access
found no struct behind the type and left out the offset of the member: with
``m`` at offset 1 the program read bytes 0 and 1 of the element (ccfuzz
seed 669).  The program is run and the values are the ones C gives.
"""

import pytest

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401

PROG = r"""
#include <stdio.h>
struct S { signed char c; int i; long l; unsigned char u; };
struct S gs[4];
unsigned char gu;
long f(void){ return (short)(gs + (gu & 3))->i; }
long g(void){ return (gs + (gu & 3))->i; }
long h(void){ return (gs + (gu & 3))->l; }
long k(int n){ return (n + gs)->l + (&gs[1] + (n - 1))->i; }
long d(void){ return (gs + 2 - 1)->i; }
int main(void)
{
  gs[0].i = -1; gs[0].l = 5; gs[0].c = 9;
  gs[1].i = 300; gs[1].l = 70000;
  printf("%ld %ld %ld\n", f(), g(), h());
  printf("%ld %ld\n", k(1), d());
  return 0;
}
"""
EXPECTED = "-1 -1 5\n70300 300\n"


@pytest.mark.parametrize("flags", [(), ("-O0",), ("--no-inlining",)])
def test_member_of_array_plus_index(tmp_path, flags):
    out = build_and_run(tmp_path, PROG, flags)
    assert out.replace("\r", "") == EXPECTED, out
