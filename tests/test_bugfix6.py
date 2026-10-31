"""Bug round 6: a macro parameter inside a string literal, and 32-bit
sums of narrow operands under --int=32."""

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401


def test_macro_parameter_is_not_replaced_inside_literals(tmp_path):
    src = r"""#include <stdio.h>
#define OUT(s) printf("%s %d\n", s, 1)
#define CH(s) printf("%c%c%s\n", 's', s, "s")
#define NAME(s) #s "-" "s"
#define PASTE(a, b) a ## b
#define VA(f, ...) printf(f "|__VA_ARGS__|" __VA_OPT__(,) __VA_ARGS__)
int main(void)
{
  int xy = 5;
  OUT("x");
  CH('q');
  puts(NAME(abc));
  printf("%d\n", PASTE(x, y));
  VA("%d", 7);
  puts("");
  return 0;
}
"""
    assert build_and_run(tmp_path, src) == \
        "x 1\nsqs\nabc-s\n5\n7|__VA_ARGS__|\n"
