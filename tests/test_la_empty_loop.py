"""L8: an empty counted loop with constant bounds becomes one assignment
(docs/family_l.md).  The tests run the program with uc80 and with the host
compiler and compare the printed counters."""

import pytest

from ._faml import needs_toolchain, run_both

pytestmark = needs_toolchain

SRC = r"""
#include <stdio.h>
int gi;
unsigned gu;
long gl;
char gc;
static int sk;
int main(void)
{
    int j, k = 5;
    unsigned u;
    unsigned char uc;
    for (gi = 0; gi < 10; ++gi) ;
    printf("%d\n", gi);
    for (j = 3; j <= 20; j += 4) ;
    printf("%d\n", j);
    for (j = 5; j < 5; j++) ;
    printf("%d\n", j);
    for (j = 9; j < 5; j++) ;
    printf("%d\n", j);
    for (gc = 0; gc < 100; gc++) ;
    printf("%d\n", gc);
    for (uc = 0; uc != 10; uc += 2) ;
    printf("%d\n", uc);
    for (u = 0; u != 10; u += 2) { }
    printf("%u\n", u);
    for (k = 0; k < 7; k++) { ; }
    printf("%d\n", k);
    for (gl = 1; gl < 32000; gl += 7) ;
    printf("%ld\n", gl);
    for (gu = 0; gu < 32767; gu += 100) ;
    printf("%u\n", gu);
    for (sk = 2; sk <= 2; sk++) ;
    printf("%d\n", sk);
    for (j = 0; j < 3; j++) k += j;
    printf("%d %d\n", j, k);
    return 0;
}
"""


def test_fold_matches_host(tmp_path):
    want, got, text = run_both(tmp_path, SRC)
    assert got == want
    assert "@FOR" in text            # the loop of a char counter stays


def test_fold_removes_the_loop(tmp_path):
    want, got, text = run_both(tmp_path, SRC)
    assert "ld\tHL,10\n\tld\t(_gi),HL" in text


def test_option_keeps_loop(tmp_path):
    want, got, text = run_both(tmp_path, SRC, "--no-empty-loop-fold")
    assert got == want
    assert "ld\tHL,10\n\tld\t(_gi),HL" not in text


def test_volatile_counter_is_kept(tmp_path):
    src = r"""
#include <stdio.h>
volatile int vv;
int main(void)
{
    for (vv = 0; vv < 3; vv++) ;
    printf("%d\n", vv);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "3"
    assert "@FOR" in text


def test_rom_build_keeps_the_delay_loop(tmp_path):
    """A ROM program waits with an empty counted loop: no fold by default."""
    import subprocess
    import sys
    from .test_struct_return_abi import REPO
    src = tmp_path / "d.c"
    src.write_text("void delay(void) { unsigned i; for (i = 0; i < 3000; i++); }\n"
                   "int main(void) { delay(); return 0; }\n")

    def mac(*flags):
        out = tmp_path / ("d%d.mac" % len(flags))
        r = subprocess.run([sys.executable, "-m", "uc80.main", str(src),
                            "-o", str(out), "--target=rom", *flags],
                           capture_output=True, text=True, cwd=str(REPO),
                           timeout=300)
        assert r.returncode == 0, r.stderr
        return out.read_text()

    assert "sub\t184" in mac()
    assert "sub\t184" not in mac("--empty-loop-fold")
