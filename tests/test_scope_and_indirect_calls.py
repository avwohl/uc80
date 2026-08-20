"""Two silent wrong answers found by attacking the return-ABI change.

Neither was caused by it -- both are older -- but both corrupt exactly what
an aggregate return travels through, so they showed up together.

  - A name declared in a block stayed visible after the block closed.  With
    a different type inside, as with a block-local function pointer, every
    later call through the outer name was emitted with the inner one's
    calling sequence.

  - Shared automatic storage is packed by asking which functions can be on
    the stack at once, and that question was answered from the direct call
    graph alone.  A function only ever reached through a pointer looked
    unrelated to its caller and was given overlapping storage, so its
    locals sat on top of the caller's.
"""

import pytest

from tests.test_struct_return_abi import build_and_run  # noqa: F401
from tests.test_struct_return_abi import pytestmark  # noqa: F401


BLOCK_SCOPE = """#include <stdio.h>
int p = 1;
int main(void) {
    { int p = 2; printf("%d\\n", p); }
    printf("%d\\n", p);
    return 0;
}
"""


def test_a_block_scoped_name_stops_at_the_brace(tmp_path):
    out = build_and_run(tmp_path, BLOCK_SCOPE, ("--printf", "int"))
    assert "2\n1\n" in out, out


SHADOWED_FUNCTION_POINTER = """#include <stdio.h>
typedef struct { int a, b, c; } Big;
typedef struct { char x, y; } Small;
Big   bf(int v) { Big b; b.a=v; b.b=v+1; b.c=v+2; return b; }
Small sf(int v) { Small s; s.x=(char)v; s.y=(char)(v+1); return s; }
Big (*p)(int) = bf;
int main(void) {
    Big g = p(1);
    printf("%d %d %d\\n", g.a, g.b, g.c);
    { Small (*p)(int) = sf; Small s = p(2); printf("%d %d\\n", s.x, s.y); }
    Big h = p(3);                 /* the file-scope p, not the block's */
    printf("%d %d %d\\n", h.a, h.b, h.c);
    return 0;
}
"""


def test_a_shadowing_pointer_does_not_outlive_its_block(tmp_path):
    """The two pointers have different return types, so using the wrong one
    means the wrong calling sequence -- with the new ABI, a copy through a
    destination the callee was never given."""
    out = build_and_run(tmp_path, SHADOWED_FUNCTION_POINTER, ("--printf", "int"))
    assert "1 2 3\n2 3\n3 4 5\n" in out, out


INDIRECT_STORAGE = """#include <stdio.h>
typedef struct { int a, b, c; } S;
S mk(int x) { S s; s.a=x; s.b=x+1; s.c=x+2; return s; }
S (*gp)(int);
int main(void) {
    gp = mk;
    S s = gp(10);
    S t = gp(20);
    printf("%d %d %d %d %d %d\\n", s.a, s.b, s.c, t.a, t.b, t.c);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("--no-shared-storage",)])
def test_a_function_reached_only_through_a_pointer_keeps_its_own_storage(
        tmp_path, flags):
    out = build_and_run(tmp_path, INDIRECT_STORAGE, flags + ("--printf", "int"))
    assert "10 11 12 20 21 22\n" in out, out
