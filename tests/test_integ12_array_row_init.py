"""A file-scope pointer initialised with a decayed row (integrate12 repair).

``short *p = m[1];`` (m is ``short m[4][5]``) is an address constant, the
label plus the row offset.  The initializer was zero, with no message.
Also ``*m + 7``, ``cm[2]``, ``m3[1][2]``, ``gs.ins[1].a``, a pointer in
a struct, an array of pointers and a static local.  Each pointer is printed
as the byte offset from its array.
"""

import pytest

from .test_famj_startup import compile_c, run_raw
from .test_struct_return_abi import _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

SRC = r"""
#include <stdio.h>
short m[4][5];
char cm[3][8];
int im[2][3];
short m3[2][3][4];
struct IN { short a[4]; };
struct GS { struct IN ins[3]; } gs;
short *g1 = m[1];
char *g2 = cm[2];
short *g3 = *m + 7;
short *g4 = (short *) m[2];
int *g5 = im[1];
short *g6 = m3[1][2];
short *g7 = gs.ins[1].a;
short *g8 = &m[2][1];
short *g9 = m[3] + 2;
struct HOLD { short *p; char *q; } hold = { m[2], cm[1] };
short *rows[] = { m[0], m[1], *m, m3[1][1] };

int main(void)
{
    static short *sl = m[3];
    printf("%d %d %d %d\n", (int) ((char *) g1 - (char *) m),
           (int) (g2 - (char *) cm), (int) ((char *) g3 - (char *) m),
           (int) ((char *) g4 - (char *) m));
    printf("%d %d %d %d\n", (int) ((char *) g5 - (char *) im),
           (int) ((char *) g6 - (char *) m3),
           (int) ((char *) g7 - (char *) &gs), (int) ((char *) g8 - (char *) m));
    printf("%d %d %d\n", (int) ((char *) g9 - (char *) m),
           (int) ((char *) hold.p - (char *) m),
           (int) (hold.q - (char *) cm));
    printf("%d %d %d %d %d\n", (int) ((char *) rows[0] - (char *) m),
           (int) ((char *) rows[1] - (char *) m),
           (int) ((char *) rows[2] - (char *) m),
           (int) ((char *) rows[3] - (char *) m3),
           (int) ((char *) sl - (char *) m));
    return 0;
}
"""


# sizes: short 2, int 2 (uc80 default), so m has rows of 10 bytes
WANT = "10 16 14 20\n6 40 8 22\n34 20 8\n0 10 0 32 30\n"


@pytest.mark.parametrize("flags", [(), ("-O0",)], ids=["default", "O0"])
def test_a_decayed_row_in_an_initializer_is_an_address(tmp_path, flags):
    mac = compile_c(tmp_path, SRC, *flags)
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out == WANT
