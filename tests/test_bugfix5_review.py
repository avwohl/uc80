"""Programs that the review of bug round 5 found wrong: a parameter with
the name of a file-scope enumerator, a block typedef in a struct body, a
tag that a block uses before it defines the tag."""

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401


def test_a_parameter_named_like_an_enumerator_is_the_parameter(tmp_path):
    src = r"""#include <stdio.h>
enum { A = 2, C = 7 };
static int f(int A) { enum { B = 3 }; return A + B; }
int g(int A) { return A + 3; }
int (*volatile pg)(int) = g;
static int h(char *C, int (*A)(int)) { return C[0] + A(1); }
static int one(int x) { return x; }
int main(void)
{
  printf("%d %d %d\n", f(10), pg(10), h("\001", one));
  printf("%d\n", A * C);
  { int A = 5; printf("%d\n", A + C); }
  return 0;
}
"""
    assert build_and_run(tmp_path, src) == \
        "13 13 2\n14\n12\n"


def test_a_block_typedef_in_a_struct_member_bound_is_the_block_typedef(
        tmp_path):
    src = r"""#include <stdio.h>
typedef int T;
int main(void)
{
  typedef char T;
  struct S { T a[sizeof(T) + 2]; } s;
  printf("%d %d\n", (int) sizeof s.a, (int) sizeof(T));
  return 0;
}
"""
    assert build_and_run(tmp_path, src) == "3 1\n"


def test_a_typedef_of_a_tag_before_the_block_definition(tmp_path):
    src = r"""#include <stdio.h>
int main(void)
{
  typedef struct T T;
  struct T { int a; char b; };
  T x = { 7, 'q' };
  printf("%d %d %c\n", (int) sizeof(T), x.a, x.b);
  return 0;
}
"""
    assert build_and_run(tmp_path, src) == "3 7 q\n"


def test_a_struct_defined_in_a_sizeof_type_name_has_its_size_later(tmp_path):
    src = r"""#include <stdio.h>
int main(void)
{
  printf("%d\n", (int) (sizeof(struct V { int a; char b; }) +
                        sizeof(struct V)));
  return 0;
}
"""
    assert build_and_run(tmp_path, src) == "6\n"
