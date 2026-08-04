"""An array bound spelled with an enum constant is folded when it matters.

_merge_array_size composites `extern int a[N];` with `int a[];` and needs
N's value to do it.  Enum constants were registered while generating
declarations, which is after generate()'s first pass has already
composited every global, so ctx.enum_constants was empty and the fold
silently did nothing:

    enum { N = 8 };
    extern int arr[N];
    int arr[];          ->  ds 0

Zero bytes, sizeof 0, and the next global sat on top of it -- writing
arr[0..7] destroyed whatever followed, with a clean exit and no
diagnostic.  The C 6.7.6.2p6 conflict check was skipped for the same
reason, so `extern int a[N]; int a[100];` compiled quietly too.

The resolved-type route cannot be used here: _to_legacy() drops an
EnumType's values, so registration walks the raw decl_specs.

tests/test_codegen.py::TestArrayRedeclaration::test_enum_constant_size
does not catch this -- both declarators there spell the size `N`, so the
emitter resolves it downstream whether or not the composite worked.
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


def compile_source(tmp_path, source, *extra_args, name="prog"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text(source)
    mac = tmp_path / (name + ".mac")
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac), *extra_args],
                       capture_output=True, text=True, cwd=str(REPO))
    return r, mac


def storage_after(mac_text, label):
    """The storage directives emitted for `label`, up to the next thing.

    Stops at the next label or at any directive that is not reserving or
    initialising bytes, so the trailing `end` does not come along.
    """
    out, seen = [], False
    for line in mac_text.split("\n"):
        if line.startswith(label + ":"):
            seen = True
            continue
        if not seen:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if not line[0].isspace():
            break
        if stripped.split("\t")[0].lower() not in ("ds", "dw", "db"):
            break
        out.append(stripped)
    return out


class TestCompositeUsesTheEnumValue:
    def test_unsized_definition_takes_the_extern_bound(self, tmp_path):
        r, mac = compile_source(tmp_path, """
enum { N = 8 };
extern int arr[N];
int arr[];
""")
        assert r.returncode == 0, r.stderr
        assert storage_after(mac.read_text(), "_arr") == ["ds\t16"]

    def test_partially_initialised_definition_is_padded(self, tmp_path):
        # The array has to be referenced, or dead-global elimination drops
        # it before there is any storage to inspect.
        r, mac = compile_source(tmp_path, """
enum { N = 8 };
extern int table[N];
int table[] = {1, 2};
int main(void){ return table[7]; }
""")
        assert r.returncode == 0, r.stderr
        # 2 initialised words plus 12 bytes of padding = the full 16.
        assert storage_after(mac.read_text(), "_table") == [
            "dw\t1", "dw\t2", "ds\t12"]

    def test_enum_with_an_explicit_value_expression(self, tmp_path):
        r, mac = compile_source(tmp_path, """
enum { BASE = 3, N = BASE + 1 };
extern int arr[N];
int arr[];
""")
        assert r.returncode == 0, r.stderr
        assert storage_after(mac.read_text(), "_arr") == ["ds\t8"]

    def test_named_enum(self, tmp_path):
        r, mac = compile_source(tmp_path, """
enum sizes { N = 5 };
extern char buf[N];
char buf[];
""")
        assert r.returncode == 0, r.stderr
        assert storage_after(mac.read_text(), "_buf") == ["ds\t5"]


class TestConflictIsDiagnosed:
    """C 6.7.6.2p6: two known, different sizes are incompatible types."""

    @pytest.mark.parametrize("other", ["100", "6"])
    def test_mismatched_size_is_an_error(self, tmp_path, other):
        r, _ = compile_source(tmp_path, """
enum { N = 8 };
extern int arr[N];
int arr[%s];
""" % other)
        assert r.returncode != 0
        assert "conflicting types for 'arr'" in r.stderr

    def test_matching_size_is_accepted(self, tmp_path):
        r, mac = compile_source(tmp_path, """
enum { N = 8 };
extern int arr[N];
int arr[8];
""")
        assert r.returncode == 0, r.stderr
        assert storage_after(mac.read_text(), "_arr") == ["ds\t16"]

    def test_no_python_traceback(self, tmp_path):
        r, _ = compile_source(tmp_path, """
enum { N = 8 };
extern int arr[N];
int arr[100];
""")
        assert "Traceback" not in r.stderr
        assert "internal error" not in r.stderr


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists()
            and (LIB_DIR / "crt0.rel").exists())


@pytest.mark.skipif(not _toolchain_available(),
                    reason="um80/ul80/cpmemu or built libraries not available")
class TestNoNeighbourCorruption:
    """The zero-byte object was silently overwritten by its neighbours."""

    def build_and_run(self, tmp_path, sources, main_name):
        rels = []
        for name, text in sources.items():
            (tmp_path / name).write_text(text)
        for name in [n for n in sources if n.endswith(".c")]:
            stem = name[:-2]
            mac = tmp_path / (stem + ".mac")
            r = subprocess.run(
                [sys.executable, "-m", "uc80.main", str(tmp_path / name),
                 "--no-whole-program", "-I", str(tmp_path), "-o", str(mac)],
                capture_output=True, text=True, cwd=str(REPO))
            assert r.returncode == 0, r.stderr
            rel = tmp_path / (stem + ".rel")
            subprocess.run(["um80", str(mac), "-o", str(rel)],
                           check=True, capture_output=True, text=True)
            rels.append(rel)
        rels.sort(key=lambda p: p.stem != main_name)
        com = tmp_path / "app.com"
        subprocess.run(["ul80", str(LIB_DIR / "crt0.rel"),
                        *[str(r) for r in rels],
                        str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                       check=True, capture_output=True, text=True)
        env = dict(os.environ, PYTHONHASHSEED="0")
        run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                             timeout=60, env=env)
        return run.stdout.decode("latin-1").replace("\r", "")

    def test_neighbours_survive(self, tmp_path):
        out = self.build_and_run(tmp_path, {"m.c": """
#include <stdio.h>
enum { N = 8 };
extern int arr[N];
int arr[];
int after1;
int after2;
int main(void){
    int i;
    after1 = 0x1111; after2 = 0x2222;
    printf("sizeof=%u\\n", (unsigned)sizeof(arr));
    for (i = 0; i < N; i++) arr[i] = 0x7777;
    printf("after1=%x after2=%x\\n", after1, after2);
    return 0;
}
"""}, "m")
        assert out == "sizeof=16\nafter1=1111 after2=2222\n"

    def test_header_declares_source_defines(self, tmp_path):
        """The realistic shape: a header bounds the array with an enum."""
        out = self.build_and_run(tmp_path, {
            "tbl.h": """
enum { NSLOT = 8 };
extern int table[NSLOT];
extern char msg[NSLOT];
int total(void);
""",
            "tbl.c": """
#include "tbl.h"
int table[] = {1, 2};
char msg[] = "hi";
int total(void){ int s = 0, i; for (i = 0; i < NSLOT; i++) s += table[i]; return s; }
""",
            "main.c": """
#include <stdio.h>
#include "tbl.h"
int main(void){
    int i;
    printf("sizeof table=%u sizeof msg=%u\\n",
           (unsigned)sizeof(table), (unsigned)sizeof(msg));
    for (i = 0; i < NSLOT; i++) table[i] = i + 1;
    printf("total=%d msg=[%s]\\n", total(), msg);
    return 0;
}
"""}, "main")
        assert out == ("sizeof table=16 sizeof msg=8\n"
                       "total=36 msg=[hi]\n")
