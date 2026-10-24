"""sscanf with %d, %x, %ld, %s and %c.

The conversions called the white-space loop as a subroutine, so the
first %d ended the program.  ungetc did nothing, the integer readers
destroyed the argument offset, %ld read zero, and a conversion without
digits still counted as a match.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"

pytestmark = pytest.mark.skipif(
    not (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
         and (LIB_DIR / "libc.lib").exists()
         and (LIB_DIR / "runtime.lib").exists()),
    reason="um80/ul80/cpmemu or built libraries not available")


def build_and_run(tmp_path, source):
    c_file = tmp_path / "prog.c"
    c_file.write_text(source)
    mac, rel, com = (tmp_path / ("prog" + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac)], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=60, env=dict(os.environ, PYTHONHASHSEED="0"))
    return run.stdout.decode("latin-1").replace("\r", "")


def test_sscanf_conversions(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    int a = -1, b = -1, c = -1, d = -1, e = -1, n;
    long L = 0, M = 0;
    char s[20], ch;
    unsigned x;
    n = sscanf("1 2 3 4 5", "%d%d%d%d%d", &a, &b, &c, &d, &e);
    printf("%d: %d %d %d %d %d\n", n, a, b, c, d, e);
    n = sscanf("  -42,+7 zz", "%d,%d", &a, &b);
    printf("%d: %d %d\n", n, a, b);
    n = sscanf("abc", "%d", &a);
    printf("%d: %d\n", n, a);
    n = sscanf("12 hello w", "%d %s %c", &a, s, &ch);
    printf("%d: %d %s %c\n", n, a, s, ch);
    n = sscanf("ff 0x1A", "%x %x", &x, &b);
    printf("%d: %x %x\n", n, x, b);
    n = sscanf("123456789 -99999", "%ld %ld", &L, &M);
    printf("%d: %ld %ld\n", n, L, M);
    n = sscanf("x=5", "x=%d", &a);
    printf("%d: %d\n", n, a);
    return 0;
}
""")
    assert out.split("\n") == [
        "5: 1 2 3 4 5", "2: -42 7", "0: -42", "3: 12 hello w",
        "2: ff 1a", "2: 123456789 -99999", "1: 5", ""]
