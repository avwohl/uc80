"""A subnormal floating constant keeps its bits.

``1e-40f`` was folded to 0 on its way to the object file.  The float
runtime flushes subnormal results of arithmetic (docs/libm.md), but a
constant the source wrote down is a value to store, move and print, so
its bits are the IEEE subnormal ones.  The expected words were checked
with a host compiler.
"""

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401

PROGRAM = '#include <stdio.h>\nunion U { float f; unsigned long u; };\nfloat sa = 1e-40f, sb = -1.4e-45f;\nfloat arr[3] = { 1e-39f, -1e-39f, 3e-45f };\nstruct P { float x; int n; } ps = { 2e-41f, 3 };\nfloat fn(void) { return 1e-40f; }\nint main(void){\n  union U a;\n  float loc = 1e-42f;\n  a.f = sa; printf("%08lx\\n", a.u);\n  a.f = sb; printf("%08lx\\n", a.u);\n  a.f = arr[0]; printf("%08lx\\n", a.u);\n  a.f = arr[1]; printf("%08lx\\n", a.u);\n  a.f = arr[2]; printf("%08lx\\n", a.u);\n  a.f = ps.x; printf("%08lx\\n", a.u);\n  a.f = loc; printf("%08lx\\n", a.u);\n  a.f = fn(); printf("%08lx\\n", a.u);\n  printf("%d %d\\n", loc > 0, sa == 1e-40f);\n  printf("%.3e\\n", (double)sa);\n  return 0;\n}\n'

EXPECTED = """000116c2
80000001
000ae398
800ae398
00000002
000037c0
000002ca
000116c2
1 1
1.000e-40
"""


def test_subnormal_constants_in_static_and_automatic_storage(tmp_path):
    assert build_and_run(tmp_path, PROGRAM) == EXPECTED


def test_a_folded_product_still_flushes_like_the_runtime(tmp_path):
    src = (
        "#include <stdio.h>\n"
        "float s = 1e-30f * 1e-10f;\n"
        "float a = 1e-30f, b = 1e-10f;\n"
        "int main(void) { float r = a * b;\n"
        "  printf(\"%d %d\\n\", s == 0.0f, r == 0.0f); return 0; }\n")
    assert build_and_run(tmp_path, src) == "1 1\n"
