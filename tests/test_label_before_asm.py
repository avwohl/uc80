"""A label before an asm statement (`L: asm("...");`) is a parse error.

C23 6.8.2 lets a label precede any statement.  The grammar lives in uplox
(examples/c23.uplox) and ships in uc_core 0.4.2; uc80 needs uc_core >= 0.4.2.
The whetstone benchmark's TIMER_START() is a label followed by an asm
statement.
"""

from tests.test_struct_return_abi import build_and_run  # noqa: F401
from tests.test_struct_return_abi import pytestmark  # noqa: F401


SRC = """#include <stdio.h>
int main(void) {
    int i = 0;
    goto LCONT;
    i = 99;
LCONT: asm("nop");
    i += 5;
LTWO: __asm__("nop");
    printf("%d\\n", i);
    return 0;
}
"""


def test_label_before_asm_statement_compiles_and_runs(tmp_path):
    out = build_and_run(tmp_path, SRC, ("--printf", "int"))
    assert "5\n" in out, out
