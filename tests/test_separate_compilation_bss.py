"""Two separately compiled units do not share one another's storage.

Uninitialized statics and the shared-automatic-storage region went into the
blank COMMON block.  That is what a common block is for -- L80 puts every
module's blank COMMON at the same address and sizes it to the largest -- so
with more than one uc80 module in the link, one unit's globals sat on top of
another's, and one unit's ??AUTO sat on top of another's live locals.  Both
were silent.

Whole-program output is a single module, where the blank COMMON is its
alone, so this only ever showed in the separate-compilation workflow the
README documents.  --no-whole-program now puts that storage in DSEG, which
the linker gives each module its own space for.
"""

import subprocess
import sys
from pathlib import Path

from tests.test_struct_return_abi import CPMEMU, LIB_DIR, REPO  # noqa: F401
from tests.test_struct_return_abi import pytestmark  # noqa: F401


def _build_two_units(tmp_path, unit_a, unit_b, flags=("--printf", "int")):
    (tmp_path / "a.c").write_text(unit_a)
    (tmp_path / "b.c").write_text(unit_b)
    for unit in ("a", "b"):
        r = subprocess.run(
            [sys.executable, "-m", "uc80.main", str(tmp_path / (unit + ".c")),
             "--no-whole-program", *flags,
             "-o", str(tmp_path / (unit + ".mac"))],
            capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        subprocess.run(["um80", str(tmp_path / (unit + ".mac")),
                        "-o", str(tmp_path / (unit + ".rel"))],
                       check=True, capture_output=True, text=True)
    subprocess.run(
        ["ul80", str(LIB_DIR / "crt0.rel"), str(tmp_path / "b.rel"),
         str(tmp_path / "a.rel"), str(LIB_DIR / "libc.lib"),
         str(LIB_DIR / "runtime.lib"), "-o", str(tmp_path / "p.com")],
        check=True, capture_output=True, text=True)
    run = subprocess.run([str(CPMEMU), str(tmp_path / "p.com")],
                         capture_output=True, timeout=120)
    return run.stdout.decode("latin-1").replace("\r", "")


GLOBALS_A = """int ga[8];
void filla(void) { int i; for (i = 0; i < 8; i++) ga[i] = 100 + i; }
"""

GLOBALS_B = """#include <stdio.h>
int gb[8];
void filla(void);
extern int ga[8];
int main(void) {
    int i;
    for (i = 0; i < 8; i++) gb[i] = 200 + i;
    filla();
    printf("%d %d %d %d\\n", gb[0], gb[7], ga[0], ga[7]);
    return 0;
}
"""


def test_each_unit_keeps_its_own_uninitialized_globals(tmp_path):
    out = _build_two_units(tmp_path, GLOBALS_A, GLOBALS_B)
    assert "200 207 100 107\n" in out, out


LOCALS_A = """void fill(int *out) {
    int scratch[40];
    int i;
    for (i = 0; i < 40; i++) scratch[i] = i * 3;
    for (i = 0; i < 8; i++) out[i] = scratch[i];
}
"""

LOCALS_B = """#include <stdio.h>
void fill(int *out);
int main(void) {
    int mine[8];
    int got[8];
    int i;
    for (i = 0; i < 8; i++) mine[i] = 500 + i;
    fill(got);
    printf("%d %d %d %d\\n", mine[0], mine[7], got[0], got[7]);
    return 0;
}
"""


def test_one_units_automatic_storage_is_not_anothers(tmp_path):
    """A called function's locals used to land on top of its caller's,
    because both units' ??AUTO started at the same address."""
    out = _build_two_units(tmp_path, LOCALS_A, LOCALS_B)
    assert "500 507 0 21\n" in out, out


def test_the_bss_of_a_separate_unit_is_zero_at_entry(tmp_path):
    """crt0 only zeroes the COMMON region, so storage that moved to DSEG
    has to arrive zeroed from the image."""
    out = _build_two_units(
        tmp_path,
        "int table[64];\nint *get_table(void) { return table; }\n",
        """#include <stdio.h>
int *get_table(void);
int main(void) {
    int *t = get_table();
    int i, sum = 0;
    for (i = 0; i < 64; i++) sum += t[i];
    printf("%d\\n", sum);
    return 0;
}
""")
    assert "0\n" in out, out


def test_whole_program_output_still_uses_common(tmp_path):
    """The single-module case keeps the bytes out of the .com image."""
    src = tmp_path / "w.c"
    src.write_text("int big[100];\nint main(void) { return big[0]; }\n")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(src),
         "-o", str(tmp_path / "w.mac")],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "w.mac").read_text()
    assert "common\t//" in text, text[-400:]
