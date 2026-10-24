"""atof and strtod: argument order, fractions, exponents, end pointer.

The last test is for pow with a whole-number exponent.

atof once pushed the two strtod arguments in the wrong order, so strtod
read the NULL end pointer as the string.  The digit accumulation was also
wrong (a fraction was dropped), the exponent factor 10.0 had its words
swapped, and a string without digits left the stack unbalanced.
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


def test_atof_and_strtod(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
static void show(double d) { printf("%ld\n", (long)(d * 1000.0)); }
int main(void)
{
    char *e;
    show(atof("2"));
    show(atof("2.5"));
    show(atof("  -12.25"));
    show(atof("1e3"));
    show(atof("2.5e-1"));
    show(atof("3.14159265358979"));
    show(atof("7"));
    show(strtod("12.5xyz", &e));
    puts(e);
    show(strtod("abc", &e));
    puts(e);
    puts("end");
    puts("a\\b");
    return 0;
}
""")
    assert out.split("\n") == [
        "2000", "2500", "-12250", "1000000", "250", "3141", "7000",
        "12500", "xyz", "0", "abc", "end", "a\\b", ""]


def test_pow_whole_exponent_is_exact(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <math.h>
int main(void)
{
    int a = 5, b = 2;
    printf("%d %d %d\n", (int)pow(a - b, 2),
           (int)sqrt(pow(a - b, 2) + pow(4, 2)), (int)pow(0, 2));
    printf("%d %d %d %d\n", (int)pow(-2, 3), (int)(pow(2, -2) * 100),
           (int)pow(7, 0), (int)pow(10, 4));
    printf("%d\n", (int)(pow(2, 0.5) * 1000));
    return 0;
}
""")
    assert out.split("\n") == ["9 5 0", "-8 25 1 10000", "1414", ""]
