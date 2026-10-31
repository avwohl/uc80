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
    ("extern struct U a[3];\nint main(void) { return sizeof a; }\n", 2),
    ("struct U;\nint main(void) {\n  return sizeof(*(struct U *) 0);\n}\n", 3),
    ("struct U;\nint main(void) {\n  return sizeof(((struct U *) 0)[0]);\n}\n",
     3),
    ("extern int a[];\nint main(void) { return sizeof a; }\n", 2),
    ("int main(void) { void *p = 0; return sizeof *p; }\n", 1),
], ids=["enum", "bound", "deref", "typedef", "union", "array", "void",
        "array-of-incomplete", "enum-expr", "extern-array-of-incomplete",
        "deref-cast", "index-cast", "extern-unsized", "void-deref"])
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


@pytest.mark.parametrize("src,text", [
    ("struct U;\nint main(void) { return sizeof(struct U[2]); }\n",
     "(struct 'U')"),
    ("struct U;\nint main(void) {\n  return sizeof(struct U (*)[2]) + "
     "sizeof(struct U[1]);\n}\n", "(struct 'U')"),
    ("int main(void) {\n  struct U;\n  return sizeof(struct U);\n}\n",
     "(struct 'U')"),
], ids=["array", "pointer-to-array", "block-scope"])
def test_the_message_names_the_tag_as_written(tmp_path, src, text):
    rc, err = compile_error(tmp_path, src)
    assert rc != 0
    assert text in err and "None" not in err and "__" not in err


FALSE_ALARMS = r"""#include <stdio.h>
typedef int A[];
int a[] = { 1, 2, 3 };
int main(void)
{
  typedef struct T T;
  struct T { int a; };
  int b[] = { 1, 2 };
  FILE *f = 0;
  printf("%d\n", (int) sizeof(T));
  printf("%d\n", (int) (sizeof(struct V { int a; }) + sizeof(struct V)));
  printf("%d %d\n", (int) sizeof a, (int) sizeof b);
  printf("%d\n", (int) sizeof(*(int (*)[3]) 0));
  printf("%d %d\n", (int) sizeof(FILE), (int) sizeof(*f));
  return 0;
}
"""


def test_a_tag_defined_later_or_in_a_type_name_is_not_an_error(tmp_path):
    """A typedef of a tag before the definition of the tag in a block, a
    definition inside a sizeof type-name, an array that an initializer
    completes, and the FILE of stdio.h (170 bytes) are all valid."""
    assert build_and_run(tmp_path, FALSE_ALARMS) == \
        "2\n4\n6 4\n6\n170 170\n"


def test_alignof_of_an_incomplete_type_is_an_error(tmp_path):
    rc, err = compile_error(
        tmp_path, "struct U;\nint main(void) { return _Alignof(struct U); }\n")
    assert rc != 0
    assert "bad.c:2: invalid application of '_Alignof' to an incomplete " \
        "type (struct 'U')" in err


def test_a_later_declaration_without_a_size_keeps_the_earlier_size(tmp_path):
    src = ("#include <stdio.h>\nextern int a[8];\nint a[];\n"
           "int main(void) { printf(\"%d\\n\", (int) sizeof a); return 0; }\n")
    assert build_and_run(tmp_path, src) == "16\n"
