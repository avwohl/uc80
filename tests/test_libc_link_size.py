"""A program links only the libc functions it calls.

libc.lib used to hold the float printer inside lc_printf_core, and several
public functions per module (lc_string, lc_stdlib, lc_malloc, lc_file ...).
Every printf() program linked the soft-float code, and every strdup() or
strlen() program linked malloc.  Each public function is now its own module
and the float printer is lc_prt_float, which the %f %e %g handlers share.
It does no float arithmetic, so it does not link the soft float runtime.

The size bound is the measured image size of printf("%d") (1498 bytes) plus
about 7 percent.  The image size is __BSS_START - 0x100 from the link
symbol file.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"

PRINTF_D_IMAGE_BOUND = 1600


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80")
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80 or built libraries not available")


def link(tmp_path, source, name="prog"):
    """Compile, assemble and link; return (com path, {symbol: address})."""
    c_file = tmp_path / (name + ".c")
    c_file.write_text(source)
    mac, rel, com, sym = (tmp_path / (name + e)
                          for e in (".mac", ".rel", ".com", ".sym"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac)], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com),
                    "-S", str(sym)],
                   check=True, capture_output=True, text=True)
    symbols = {}
    for line in sym.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            symbols[parts[1]] = int(parts[0], 16)
    return com, symbols


def image_size(symbols):
    return symbols["__BSS_START"] - 0x100


FLOAT_SYMBOLS = ("__PRINTF_HANDLE_F", "__FMUL", "__FADD", "__FTOI", "__ITOF")


def test_printf_d_links_no_float_code(tmp_path):
    _, syms = link(tmp_path, '#include <stdio.h>\n'
                   'int main(void){ int x = 5; printf("%d\\n", x); return 0; }\n')
    for name in FLOAT_SYMBOLS:
        assert name not in syms, name + " linked into a printf(%d) program"
    assert image_size(syms) <= PRINTF_D_IMAGE_BOUND


def test_strlen_does_not_link_malloc(tmp_path):
    _, syms = link(tmp_path, '#include <string.h>\n'
                   'int main(void){ return (int)strlen("abc"); }\n')
    assert "_STRLEN" in syms
    for name in ("_MALLOC", "_STRDUP", "_FREE", "_STRCPY"):
        assert name not in syms, name + " linked into a strlen-only program"


def test_strdup_pulls_malloc_and_strlen(tmp_path):
    _, syms = link(tmp_path, '#include <string.h>\n'
                   'int main(void){ return strdup("abc") != 0; }\n')
    assert "_STRDUP" in syms and "_MALLOC" in syms and "_STRLEN" in syms


def test_exit_does_not_link_rand_or_abort(tmp_path):
    _, syms = link(tmp_path, '#include <stdlib.h>\n'
                   'int main(void){ exit(3); }\n')
    for name in ("_RAND", "_SRAND", "_ABORT", "_DIV", "_LDIV"):
        assert name not in syms, name + " linked into an exit-only program"


def test_percent_f_still_links_float_printer(tmp_path):
    _, syms = link(tmp_path, '#include <stdio.h>\n'
                   'volatile float v = 1.5f;\n'
                   'int main(void){ printf("%f\\n", v); return 0; }\n')
    assert "__PRINTF_HANDLE_F" in syms
    # the exact digits come from integer arithmetic
    for name in ("__FMUL", "__FADD", "__FDIV", "__FTOI", "__ITOF"):
        assert name not in syms, name + " linked into a printf(%f) program"


@pytest.mark.skipif(not CPMEMU.exists(), reason="cpmemu not available")
@pytest.mark.parametrize("fmt, value, expected", [
    ("%f", "3.14159f", "3.141590"),
    ("%.2f", "2.5f", "2.50"),
    ("%f", "-0.5f", "-0.500000"),
    ("%e", "3.14159f", "3.141590e+00"),
    ("%e", "12345.0f", "1.234500e+04"),
    ("%g", "3.14159f", "3.14159"),
    ("%g", "100000.0f", "100000"),
    ("%.1f", "1000000.0f", "1000000.0"),
])
def test_float_formats_output(tmp_path, fmt, value, expected):
    com, _ = link(tmp_path, '#include <stdio.h>\n'
                  'volatile float v = %s;\n'
                  'int main(void){ printf("%s|", v); return 0; }\n'
                  % (value, fmt))
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         text=True, timeout=30)
    assert expected + "|" in run.stdout, run.stdout


@pytest.mark.skipif(not CPMEMU.exists(), reason="cpmemu not available")
def test_split_libc_functions_run(tmp_path):
    """Functions from the split modules still work together."""
    com, _ = link(tmp_path, '''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
int main(void) {
    char *s = strdup("Hello");
    char *t = malloc(16);
    strcpy(t, s);
    strcat(t, "x");
    printf("%s %d %ld %d %d\\n", t, atoi("42"), strtol("-17", 0, 10),
           toupper('a'), abs(-4));
    free(s);
    free(t);
    return 0;
}
''')
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         text=True, timeout=30)
    assert "Hellox 42 -17 65 4" in run.stdout, run.stdout


QSORT_SRC = '''#include <stdlib.h>
#include <stdio.h>
#include <stdarg.h>
static int cmp(const void *a, const void *b)
{ return *(const int *)a - *(const int *)b; }
int main(void) {
    int v[5] = {5, 3, 9, 1, 7};
    qsort(v, 5, sizeof v[0], cmp);
    printf("%d %d %d\\n", v[0], v[2], v[4]);
    int key = 7;
    int *p = bsearch(&key, v, 5, sizeof v[0], cmp);
    printf("%d\\n", p ? (int)(p - v) : -1);
    srand(3);
    printf("%d\\n", rand());
    return 0;
}
'''


def test_qsort_does_not_link_bsearch(tmp_path):
    _, syms = link(tmp_path, '#include <stdlib.h>\n'
                   'static int c(const void *a, const void *b){return 0;}\n'
                   'int main(void){ int v[2]; qsort(v,2,2,c); return 0; }\n')
    assert "_QSORT" in syms and "_BSEARCH" not in syms


def test_rand_does_not_link_runtime_multiply(tmp_path):
    _, syms = link(tmp_path, '#include <stdlib.h>\n'
                   'int main(void){ srand(1); return rand() & 1; }\n')
    assert "_RAND" in syms
    for name in ("__MUL16", "__DIV16", "__MOD16"):
        assert name not in syms, name + " linked into a rand-only program"


def test_fprintf_does_not_link_vsprintf(tmp_path):
    _, syms = link(tmp_path, '#include <stdio.h>\n'
                   'int main(void){ fprintf(stdout, "%d\\n", 5); return 0; }\n')
    assert "_FPRINTF" in syms
    for name in ("_VPRINTF", "_VSPRINTF", "_SPRINTF"):
        assert name not in syms, name + " linked into an fprintf-only program"


@pytest.mark.skipif(not CPMEMU.exists(), reason="cpmemu not available")
def test_qsort_bsearch_rand_run(tmp_path):
    com, _ = link(tmp_path, QSORT_SRC)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         text=True, timeout=30)
    # seed 3: (3 * 25173 + 13849) & 0x7FFF
    assert run.stdout.split() == ["1", "5", "9", "3",
                                  str((3 * 25173 + 13849) & 0x7FFF)], run.stdout
