"""pointer +/- a long index was not scaled by the element size.

`short *b = a + n * n;` with `unsigned long n` took the 32-bit binary-op
path, which adds the integer as a byte count.  `b - a` came out as 40, not
81.  (Found by the coremark benchmark: core_init_matrix does
`B = A + N * N` with `ee_u32 N`, so matrix B overlapped matrix A and the
list, matrix and state CRCs were wrong.)
"""

from tests.test_struct_return_abi import build_and_run  # noqa: F401
from tests.test_struct_return_abi import pytestmark  # noqa: F401


SRC = """#include <stdio.h>
static short a[200];
struct S { char c[5]; };
static struct S s[40];
static int walk(unsigned char *base, unsigned long len, short step) {
    unsigned char *p = base;
    int n = 0;
    while (p < (base + len)) { n++; p += step; }
    return n;
}
static unsigned char buf[200];
int main(void) {
    unsigned long n = 9;
    long m = 9, k = 3;
    short *b = a + n * n;
    short *c = a + n;
    short *d = m + a;
    short *e = &a[100] - m * k;
    struct S *f = s + n;
    struct S *g = &s[30] - k;
    short *h = a;
    h += n;
    printf("%d %d %d %d\\n", (int)(b - a), (int)(c - a), (int)(d - a),
           (int)(e - a));
    printf("%d %d %d\\n", (int)(f - s), (int)(g - s), (int)(h - a));
    printf("%d\\n", (int)((char *)b - (char *)a));
    printf("%d %d\\n", walk(buf, 100, 34), walk(buf, 100, 7));
    return 0;
}
"""


def test_pointer_plus_or_minus_a_long_scales_by_element_size(tmp_path):
    out = build_and_run(tmp_path, SRC, ("--printf", "int"))
    assert "81 9 9 73\n" in out, out
    assert "9 27 9\n" in out, out
    assert "162\n" in out, out
    # a pointer compared with `base + len` (the coremark state machine):
    # the sum used to be taken as a 32-bit value and the loop never ended
    assert "3 15\n" in out, out
