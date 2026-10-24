"""div() and ldiv() (lc_stdlib.mac) against C's truncating division.

div() passed its operands to __sdiv16 in the wrong registers (that routine
computes DE / HL), so it returned denom / numer: div(4425, 3) gave 0 rem 3.
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
    reason="um80/ul80/cpmemu or the built libraries are missing")

PAIRS16 = [(4425, 3), (3, 4425), (-251, 48), (251, -48), (-251, -48), (0, 7),
           (32767, 1), (32767, -32767), (-32767, 117), (296, 32767),
           (7, 7), (-32768, 3), (-32768, -3), (-32767, -1)]
PAIRS32 = [(100000, 7), (-100000, 7), (100000, -7), (7, 100000),
           (2147483647, 1), (2147483647, -2147483647), (-2147483647, 65536),
           (123456789, 1000), (-5, 3), (5, -3)]


def tdiv(a, b):
    q = abs(a) // abs(b)
    q = -q if (a < 0) != (b < 0) else q
    return q, a - q * b


def lit(v, suffix):
    return "(%d%s)" % (v, suffix)


def test_div_and_ldiv(tmp_path):
    lines = []
    for a, b in PAIRS16:
        lines.append('{ div_t d = div(%s, %s); printf("%%d %%d\\n", d.quot, d.rem); }'
                     % ("(-32767-1)" if a == -32768 else a, b))
    for a, b in PAIRS32:
        lines.append('{ ldiv_t d = ldiv(%s, %s); printf("%%ld %%ld\\n", d.quot, d.rem); }'
                     % (lit(a, "L"), lit(b, "L")))
    src = ("#include <stdio.h>\n#include <stdlib.h>\nint main(void) {\n"
           + "\n".join(lines) + "\nreturn 0; }\n")
    c_file = tmp_path / "d.c"
    c_file.write_text(src)
    mac, rel, com = (tmp_path / ("d" + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file), "-o", str(mac)],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)], check=True, capture_output=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True, text=True,
                         timeout=60, env=dict(os.environ, PYTHONHASHSEED="0"))
    want = ["%d %d" % tdiv(a, b) for a, b in PAIRS16 + PAIRS32]
    assert run.stdout.replace("\r", "").split("\n")[:len(want)] == want
