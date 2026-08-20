"""What a function's frame has to hold, and where each thing in it lives.

Three defects here shared a shape: the value read back was garbage or
belonged to something else, and nothing was reported.

  - Frame sizing enumerated the statement kinds it knew how to look inside,
    so a declaration under a label or a ``default:`` reserved no space and
    landed below SP, where the next PUSH wrote over it.  It also sized each
    declarator from its declared type alone, so ``char s[] = "hello"``
    counted as nothing.

  - An unnamed parameter -- ``int f(int, int x)``, or an abstract ``char *``
    -- was skipped instead of being stepped over, so every parameter after
    it read the argument before it.

  - A local array with index designators was filled positionally:
    ``int a[] = {[9]=91, [0]=2}`` put 91 in a[0].  The same declaration at
    file scope was always right, so the two storage durations disagreed.

--no-shared-storage forces stack frames, which is where the first two
showed: a function eligible for shared storage got its size from the call
graph analyzer, which sized unsized arrays correctly.
"""

import pytest

from tests.test_struct_return_abi import build_and_run  # noqa: F401
from tests.test_struct_return_abi import pytestmark  # noqa: F401


DECL_UNDER_A_LABEL = """#include <stdio.h>
void f(int n) {
    if (n == 0) goto done;
    f(n - 1);
done: {
        int a[8]; int i;
        for (i = 0; i < 8; i++) a[i] = n * 10 + i;
        printf("%d %d %d\\n", a[0], a[4], a[7]);
    }
}
void g(int n) {
    switch (n) {
    default: {
            int b[8]; int i;
            for (i = 0; i < 8; i++) b[i] = n * 100 + i;
            if (n) g(n - 1);
            printf("%d %d %d\\n", b[0], b[4], b[7]);
        }
    }
}
int main(void) { f(1); g(1); return 0; }
"""


def test_a_declaration_under_a_label_gets_frame_space(tmp_path):
    out = build_and_run(tmp_path, DECL_UNDER_A_LABEL, ("--no-shared-storage",))
    assert "0 4 7\n10 14 17\n" in out, out          # f(0) then f(1)
    assert "0 4 7\n100 104 107\n" in out, out       # g(0) then g(1)


UNSIZED_ARRAY = """#include <stdio.h>
void f(int n) {
    char s[] = "hello world";
    if (n) f(n - 1);
    printf("%d [%s]\\n", n, s);
}
int main(void) { f(1); return 0; }
"""


def test_an_unsized_array_is_sized_from_its_initializer(tmp_path):
    """The bytes are written into the frame at a declaration's offset; if
    the prologue reserved nothing, the first argument push destroys them."""
    out = build_and_run(tmp_path, UNSIZED_ARRAY, ("--no-shared-storage",))
    assert "0 [hello world]\n1 [hello world]\n" in out, out


UNNAMED_PARAMS = """#include <stdio.h>
int second(int, int x) { return x; }
int third(int, char *, long v) { return (int)v; }
int main(void) {
    printf("%d %d\\n", second(111, 222), third(1, 0, 333L));
    return 0;
}
"""


def test_an_unnamed_parameter_still_occupies_its_slot(tmp_path):
    out = build_and_run(tmp_path, UNNAMED_PARAMS,
                        ("--no-inlining", "--no-const-propagation"))
    assert "222 333\n" in out, out


DESIGNATED = """#include <stdio.h>
static void show(int *a) {
    int i;
    for (i = 0; i < 10; i++) printf("%d,", a[i]);
    printf("\\n");
}
int main(void) {
    int local[] = {[9] = 91, [0] = 2};
    static int global[] = {[9] = 91, [0] = 2};
    show(local);
    show(global);
    return 0;
}
"""


def test_a_local_array_honours_index_designators(tmp_path):
    """Automatic and static storage have to agree on the same
    initializer."""
    out = build_and_run(tmp_path, DESIGNATED)
    expected = "2,0,0,0,0,0,0,0,0,91,\n"
    assert out.count(expected) == 2, out


DEEP_FRAME = """#include <stdio.h>
int deep(int n) {
    int pad[70];              /* 140 bytes, so what follows is past IX-128 */
    int tail;
    long wide;
    int i;
    tail = n * 100;
    wide = n * 1000L;
    for (i = 0; i < 70; i++) pad[i] = n;
    if (n) deep(n - 1);
    return tail + (int)wide + pad[0] + pad[69];
}
int main(void) { printf("%d %d\\n", deep(2), deep(0)); return 0; }
"""


def test_a_local_past_the_ix_displacement_is_still_reachable(tmp_path):
    """(IX+d) holds a signed byte.  A local further than that from the frame
    pointer used to be assembled with the low byte of the offset, so the
    access landed in the caller's frame -- and for a frame over 256 bytes,
    two locals aliased each other.  um80 assembles the operand without
    complaint, so nothing said anything."""
    out = build_and_run(tmp_path, DEEP_FRAME, ("--no-shared-storage",))
    assert "2204 0\n" in out, out


ALIASING_FRAME = """#include <stdio.h>
int f(void) {
    int first;
    int pad[130];             /* 260 bytes: first and last alias mod 256 */
    int last;
    int i;
    first = 11; last = 22;
    for (i = 0; i < 130; i++) pad[i] = 0;
    return first * 1000 + last;
}
int main(void) { printf("%d\\n", f()); return 0; }
"""


def test_two_locals_a_frame_apart_do_not_alias(tmp_path):
    out = build_and_run(tmp_path, ALIASING_FRAME, ("--no-shared-storage",))
    assert "11022\n" in out, out
