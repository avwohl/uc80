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
