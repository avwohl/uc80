"""sizeof of an incomplete type is an error with file:line (C23 6.5.4.5),
also in an enumerator and in an array bound.  A complete type, also one
that is completed later, still works and runs."""

import pytest

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401
from tests.test_integrate11_repair import compile_error


@pytest.mark.parametrize("src,line", [
    ("enum E { A = sizeof(struct Undefined) };\nint main(void) { return A; }\n",
     1),
    ("struct U;\nint a[sizeof(struct U)];\nint main(void) { return 0; }\n", 2),
    ("struct U;\nint f(struct U *p) {\n  return sizeof(*p);\n}\n"
     "int main(void) { return 0; }\n", 3),
    ("typedef struct U U;\nint main(void) {\n  return sizeof(U);\n}\n", 3),
    ("union V;\nint main(void) {\n\n  return sizeof(union V);\n}\n", 4),
    ("int main(void) { return sizeof(int[]); }\n", 1),
    ("int main(void) { return sizeof(void); }\n", 1),
    ("struct U;\nint main(void) { return sizeof(struct U[3]); }\n", 2),
    ("struct U;\nstatic const int n = 1;\nenum { B = 2 * "
     "sizeof(struct U) };\nint main(void) { return B; }\n", 3),
], ids=["enum", "bound", "deref", "typedef", "union", "array", "void",
        "array-of-incomplete", "enum-expr"])
def test_sizeof_of_an_incomplete_type_is_an_error(tmp_path, src, line):
    rc, err = compile_error(tmp_path, src)
    assert rc != 0
    assert "bad.c:%d: invalid application of 'sizeof' to an incomplete " \
        "type" % line in err


COMPLETE = r"""#include <stdio.h>
struct S;
struct S { int a; char b; };
typedef struct T T;
struct T { long x; };
enum E { A = sizeof(struct S), B = sizeof(T) * 2 };
int arr[] = { 1, 2, 3 };
int main(void)
{
  struct S *p = 0;
  struct { int q[4]; } anon;
  printf("%d %d %d %d\n", A, B, (int) sizeof(*p), (int) sizeof(anon));
  printf("%d\n", (int) sizeof(arr));
  { struct S { char c; }; printf("%d\n", (int) sizeof(struct S)); }
  return 0;
}
"""


def test_a_complete_type_is_not_an_error(tmp_path):
    assert build_and_run(tmp_path, COMPLETE) == "3 8 3 8\n6\n1\n"
