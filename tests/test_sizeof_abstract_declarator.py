"""sizeof(void *) inside a call argument stopped the compiler.

Constant-parameter propagation folds the call arguments with
`_eval_const_expr`.  A `sizeof(T)` whose type-name has an abstract
declarator (`void *`, `char[4]`) arrives as `TypeNameWithDeclarator`, and
`_var_size_r` read `.kind` from that node: "internal error:
'TypeNameWithDeclarator' object has no attribute 'kind'".
(Found by the embench-tarfind benchmark.)
"""

from tests.test_struct_return_abi import build_and_run  # noqa: F401
from tests.test_struct_return_abi import pytestmark  # noqa: F401


SRC = """#include <stdio.h>
static int g;
static void f(int x) { g += x; }
int main(void) {
    f((10 + 3) * sizeof(void *));
    f(sizeof(char[7]) + 4);
    printf("%d\\n", g);
    return 0;
}
"""


def test_sizeof_pointer_and_array_type_names_in_call_arguments(tmp_path):
    out = build_and_run(tmp_path, SRC, ("--printf", "int"))
    assert "37\n" in out, out
