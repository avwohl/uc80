"""A user _putchar linked as a separate module (integrate12 repair).

The whole-program build calls __putchar_v for ``putchar(c);``.  A module
that is linked after the compile, and that defines _putchar, is not seen,
so the default build does not call that routine.  The option
``--no-void-putchar`` keeps the call.  docs/void_putchar.md says so.
"""

import re
import subprocess
import sys

import pytest

from .test_struct_return_abi import CPMEMU, LIB_DIR, REPO, _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

MAIN = r"""
extern int putchar(int);
int main(void)
{
    putchar('A');
    putchar('B');
    (void) putchar('C');
    return 0;
}
"""

# the stack convention: the character is the word at SP+2; the routine
# prints the character plus one
USER = """\t.Z80
\tCSEG
\tPUBLIC\t_putchar
_putchar:
\tLD\tHL,2
\tADD\tHL,SP
\tLD\tE,(HL)
\tINC\tE
\tLD\tC,2
\tCALL\t5
\tLD\tHL,0
\tRET
\tEND
"""


def build_run(tmp_path, *flags):
    (tmp_path / "main.c").write_text(MAIN)
    (tmp_path / "up.mac").write_text(USER)
    r = subprocess.run([sys.executable, "-m", "uc80.main",
                        str(tmp_path / "main.c"), *flags, "-o",
                        str(tmp_path / "main.mac")],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    for n in ("main", "up"):
        subprocess.run(["um80", str(tmp_path / (n + ".mac")), "-o",
                        str(tmp_path / (n + ".rel"))], check=True,
                       capture_output=True)
    subprocess.run(["ul80", str(tmp_path / "main.rel"),
                    str(tmp_path / "up.rel"), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o",
                    str(tmp_path / "t.com")], check=True, capture_output=True)
    out = subprocess.run([str(CPMEMU), str(tmp_path / "t.com")],
                         capture_output=True, timeout=120).stdout
    out = re.sub(rb"^CPU mode: [^\n]*\n", b"", out)
    out = re.sub(rb"^Loaded [^\n]*\n", b"", out)
    out = re.sub(rb"Program exit[^\n]*\n?$", b"", out)
    return out.decode("latin-1").strip()


def test_no_void_putchar_calls_the_linked_user_routine(tmp_path):
    assert build_run(tmp_path, "--no-void-putchar") == "BCD"


def test_default_build_prints_through_the_library(tmp_path):
    assert build_run(tmp_path) == "ABC"
