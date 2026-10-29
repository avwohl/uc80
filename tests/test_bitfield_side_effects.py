"""A bit-field read-modify-write evaluates its object expression once.

``a[i++].y++``, ``++a[i++].y``, ``a[i++].y += 2`` and ``pf()->x += 9`` used
to evaluate ``a[i++]`` or ``pf()`` up to three times (the read, the
operation and the write each took the address again), so ``i`` ended at 3
and ``pf`` ran three times.  The expected text came from a host compiler.
The right side calls a function that does the same kind of update on another
element, to show that the shared pointer survives a nested use.
"""

import pytest

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401

PROGRAM = '#include <stdio.h>\nstruct S { unsigned x:3; unsigned y:13; int p; } a[4];\nstruct L { unsigned long u:20; long v:12; } lw[3];\nstruct S *ps;\nint i, cnt;\nstruct S *pf(void) { cnt++; return &a[1]; }\nstruct L *pl(void) { cnt++; return &lw[1]; }\nint g(int n);\nint main(void)\n{\n  int r; long q;\n  i = 0; a[0].y = 3; r = a[i++].y++; printf("%d %d %d\\n", r, i, a[0].y);\n  i = 0; r = ++a[i++].y; printf("%d %d %d\\n", r, i, a[0].y);\n  i = 0; r = a[i++].y--; printf("%d %d %d\\n", r, i, a[0].y);\n  i = 0; r = --a[i++].y; printf("%d %d %d\\n", r, i, a[0].y);\n  i = 0; r = (a[i++].y += 2); printf("%d %d %d\\n", r, i, a[0].y);\n  i = 0; a[i++].y |= 8; printf("%d %d\\n", i, a[0].y);\n  i = 0; a[i++].y++; printf("%d %d\\n", i, a[0].y);\n  i = 2; a[--i].y = 9; a[i--].x ^= 5; printf("%d %d %d\\n", i, a[1].y, a[1].x);\n  cnt = 0; pf()->y++; printf("%d %d\\n", cnt, a[1].y);\n  cnt = 0; r = (pf()->x += 9); printf("%d %d %d\\n", r, cnt, a[1].x);\n  cnt = 0; q = (pl()->u += 70000); printf("%ld %d %lu\\n", q, cnt, (unsigned long)lw[1].u);\n  cnt = 0; q = pl()->v--; printf("%ld %d %ld\\n", q, cnt, (long)lw[1].v);\n  cnt = 0; q = ++pl()->v; printf("%ld %d %ld\\n", q, cnt, (long)lw[1].v);\n  ps = a; r = (ps++)->y++; printf("%d %d\\n", r, (int)(ps - a));\n  ps = a; r = ((ps++)->x += 4); printf("%d %d\\n", r, (int)(ps - a));\n  i = 0; r = (a[i++].y += g(3)); printf("%d %d %d %d %d\\n", r, i, a[0].y, a[3].y, a[3].x);\n  return 0;\n}\nint j;\nint g(int n)\n{\n  if (n) { a[3 + j++ * 0].y += 2; a[3].x++; g(n - 1); a[2 + j++].x ^= 1; j = 0; }\n  return n;\n}\n'
EXPECT = '3 1 4\n5 1 5\n5 1 4\n3 1 3\n5 1 5\n1 13\n1 14\n0 9 5\n1 10\n6 1 6\n70000 1 70000\n0 1 -1\n0 1 0\n14 1\n4 1\n18 1 18 6 3\n'


@pytest.mark.parametrize("flags", [
    (), ("--int", "32"), ("-O0",), ("--no-shared-storage", "--int", "32"),
    ("--isr-safe",)], ids=["int16", "int32", "O0", "stack-int32", "isr-safe"])
def test_a_bit_field_update_evaluates_its_object_once(tmp_path, flags):
    assert build_and_run(tmp_path, PROGRAM, flags) == EXPECT
