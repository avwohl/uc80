"""An expression whose value is a struct has to be copied, not stored.

A call returning a struct, a member read, a conditional, a cast: each
designates bytes somewhere, and `gen_expr` on one yields the address of
those bytes.  Wherever an initializer or an assignment expected a scalar it
stored that address into the first field and left the rest zero, so
`struct S a[] = { mk(7), mk(9) };` filled the array with two pointers.  A
conditional was worse -- `_gen_address` had no case for it and no fallback,
so nothing at all was emitted and the member came out of whatever HL held.

All of it was silent.
"""

import pytest

from tests.test_struct_return_abi import CONFIGS, build_and_run  # noqa: F401
from tests.test_struct_return_abi import pytestmark  # noqa: F401


SOURCE = """#include <stdio.h>
struct S { int a, b, c; };
struct O { struct S in; int z; };
union  U { struct S in; char raw[6]; };
struct S mk(int v) { struct S s; s.a=v; s.b=v+1; s.c=v+2; return s; }
struct S q = {1, 2, 3}, r = {4, 5, 6};

int pick(int c) { return (c ? q : r).b; }

int main(void) {
    struct S arr[2] = { mk(7), mk(9) };
    printf("%d %d %d %d\\n", arr[0].a, arr[0].c, arr[1].a, arr[1].c);

    struct O o = { mk(3), 5 };
    printf("%d %d %d\\n", o.in.a, o.in.c, o.z);

    union U u = { mk(4) };
    printf("%d %d\\n", u.in.a, u.in.c);

    struct S d[3] = { [1] = mk(6) };
    printf("%d %d %d\\n", d[0].a, d[1].a, d[1].c);

    struct S s;
    s = (struct S)q;
    printf("%d %d %d\\n", s.a, s.b, s.c);

    s = mk(20);
    printf("%d %d %d\\n", s.a, s.b, s.c);

    printf("%d %d\\n", pick(1), pick(0));
    return 0;
}
"""

EXPECTED = ("7 9 9 11\n"        # array elements from calls
            "3 5 5\n"           # struct member from a call
            "4 6\n"             # union first member from a call
            "0 6 8\n"           # designated element from a call
            "1 2 3\n"           # cast to the aggregate's own type
            "20 21 22\n"        # plain assignment from a call
            "2 5\n")            # conditional yielding a struct


@pytest.mark.parametrize("flags", CONFIGS)
def test_an_aggregate_value_is_copied_wherever_it_appears(tmp_path, flags):
    out = build_and_run(tmp_path, SOURCE, flags + ("--printf", "int"))
    assert EXPECTED in out, out


CHAINED = """#include <stdio.h>
typedef struct { int a, b, c; } S;
S f(int x) { S s; s.a=x; s.b=x+1; s.c=x+2; return s; }
int main(void) {
    S u = {1,2,3}, t = {9,9,9}, s = {8,8,8};
    s = t = u;
    printf("%d %d %d %d %d %d\\n", s.a, s.b, s.c, t.a, t.b, t.c);

    S w = ({ S q = f(4); q; });
    printf("%d %d %d\\n", w.a, w.b, w.c);

    S z;
    z = ({ S q = f(7); q; });
    printf("%d %d %d\\n", z.a, z.b, z.c);
    return 0;
}
"""


def test_an_assignment_and_a_statement_expression_designate_their_value(tmp_path):
    """Both are expressions whose value is a struct, and both were shapes
    _gen_address had no case for -- so nothing was emitted and the copy
    read through whatever HL held."""
    out = build_and_run(tmp_path, CHAINED, ("--printf", "int"))
    assert "1 2 3 1 2 3\n4 5 6\n7 8 9\n" in out, out


BLOCK_SCOPE_TAG = """#include <stdio.h>
void f(int n) {
    struct L { int p, q, r, s; };
    struct L x;
    int keep;
    x.p = 1; x.q = 2; x.r = 3; x.s = 4;
    keep = 4242;
    if (n) f(n - 1);
    printf("%d %d %d\\n", x.p, x.s, keep);
}
int main(void) { f(1); return 0; }
"""


@pytest.mark.parametrize("flags", [(), ("--no-shared-storage",)])
def test_a_tag_defined_in_the_body_is_sized_before_the_frame(tmp_path, flags):
    """Frame sizing runs before the body is generated, and the tag was not
    registered yet, so _type_size said 0 and the next local was laid on top
    of the struct."""
    out = build_and_run(tmp_path, BLOCK_SCOPE_TAG, flags + ("--printf", "int"))
    assert out.count("1 4 4242\n") == 2, out


TYPEDEF_LEAK = """#include <stdio.h>
typedef struct { int a, b, c; } S;
S mk(int v) { S s; s.a=v; s.b=v+1; s.c=v+2; return s; }
void first(void) { typedef int W[8]; W q; q[0] = 1; printf("%d\\n", q[0]); }
void second(void) {
    int v[8]; int i; int keep; S r;
    for (i = 0; i < 8; i++) v[i] = i;
    keep = 4242;
    r = mk(50);
    printf("%d %d %d\\n", keep, r.a, v[7]);
}
int main(void) { first(); second(); return 0; }
"""


def test_a_typedef_does_not_leak_into_the_next_function(tmp_path):
    """A name declared inside a function is not visible in the next one.
    Leaving it registered let frame sizing and the shared-storage plan --
    which run at different times -- size the same declaration two ways, so
    a slot landed past the end of the region it was allocated in."""
    out = build_and_run(tmp_path, TYPEDEF_LEAK, ("--printf", "int"))
    assert "1\n4242 50 7\n" in out, out
