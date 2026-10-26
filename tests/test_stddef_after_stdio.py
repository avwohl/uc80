"""<stddef.h> after <stdio.h> still defines ptrdiff_t.

stdio.h, string.h, stdlib.h and time.h define size_t under _SIZE_T_DEFINED,
and stddef.h defined ptrdiff_t under the same guard, so ptrdiff_t was missing
when another header came first.
"""

from ._clib import build_and_run, skip_without_tools

pytestmark = skip_without_tools


def test_ptrdiff_t_after_stdio_and_string(tmp_path):
    for first in ("stdio.h", "string.h", "stdlib.h", "time.h"):
        out = build_and_run(tmp_path, f"""
#include <{first}>
#include <stddef.h>
#include <stdio.h>
int main(void)
{{
    ptrdiff_t d = -3;
    size_t n = 5;
    printf("%d %u %u\\n", (int)d, (unsigned)n, (unsigned)sizeof(ptrdiff_t));
    return 0;
}}
""")
        assert out.split("\n")[0] == "-3 5 2", first
