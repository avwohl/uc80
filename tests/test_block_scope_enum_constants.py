"""Enumeration constants are block scoped (C23 6.2.1p4).

An enum declared in a block hides a file-scope constant of the same name
inside the block only, and a variable declared in a block hides a constant
too.  The expected text was produced by a host compiler.
"""

import pytest

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401

PROGRAM = r'''#include <stdio.h>
enum E { X1 = 5, X2 };
int X3 = 9;
struct T { int X2; int v[2]; };
int main(void)
{
  struct T t;
  printf("%d %d\n", X1, X2);
  {
    enum F { X1 = 77, Y = X1 + 1, X2 };
    struct { int X3; char c[X1 - 70]; } u;
    int q[Y - 76];
    u.X3 = Y;
    t.X2 = 4;
    printf("%d %d %d %d %d %d\n", X1, Y, X2, u.X3, t.X2, (int)sizeof q / (int)sizeof(int));
    printf("%d %d\n", (int)sizeof u.c, X3);
    {
      int X1 = 3;
      enum { Y = 100 };
      printf("%d %d\n", X1, Y);
    }
    printf("%d %d\n", X1, Y);
  }
  printf("%d %d %d\n", X1, X2, X3);
  return 0;
}'''
EXPECT = r'''5 6
77 78 79 78 4 2
7 9
3 100
77 78
5 6 9
'''


@pytest.mark.parametrize("flags", [(), ("--int", "32")], ids=["int16", "int32"])
def test_a_block_enum_and_a_hiding_variable_leave_file_scope_alone(
        tmp_path, flags):
    assert build_and_run(tmp_path, PROGRAM, flags) == EXPECT
