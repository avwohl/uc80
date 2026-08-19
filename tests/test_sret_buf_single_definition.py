"""__sret_buf is defined by exactly one module, so the documented link line
does not define it twice.

The struct-return buffer sat in the DSEG of rt_arith32.mac, next to __tmp32
and the 32-bit multiply/divide work areas, because that is where it landed
when runtime.mac was first split into modules.  It has nothing to do with
32-bit arithmetic -- every function returning a struct by value leaves its
result there -- and sharing that module gave it two homes: the runtime the
compiler embeds into the object, and rt_arith32 inside runtime.lib.

Returning a struct made the compiler embed the buffer; anything that later
pulled rt_arith32 out of the library brought a second definition with it.
Printing a long is enough, because libc reaches the 32-bit helpers on its
own and the compiler's embed only ever sees what the generated code refers
to.  L80 keeps the first definition and links on, so nothing was said until
um80 0.3.46 stopped dropping a recorded error, and the second buffer was
still allocated -- 256 bytes of every affected binary, never read.

__sret_buf is uniquely exposed among the shared PUBLIC data symbols because
generated code references it without embedding any rt_arith32 *function*.
For __tmp32 and the float work areas, embedding the symbol means embedding
the functions that use it, which is what stops the module being pulled.

ul80 exits 0 here -- L80 accepts this link and produces output -- so these
tests read what the linker printed rather than its status, and they drive
the ul80 command line, not the Linker API.  The API is what tests/ already
covered when the duplicate shipped.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
RT_DIR = LIB_DIR / "rt"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80")
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


# A struct return puts __sret_buf in the object; the %ld pulls the 32-bit
# helpers in through libc, which is what used to drag rt_arith32 -- and its
# own __sret_buf -- into the same link.
SOURCE = """#include <stdio.h>
struct big { int a; char pad[200]; int b; };
struct big make(int t) {
    struct big s;
    s.a = t; s.b = t * 2; s.pad[0] = (char)t; s.pad[199] = (char)(t + 1);
    return s;
}
int main(void) {
    struct big r = make(21);
    printf("a=%d b=%d p0=%d p199=%d\\n", r.a, r.b, (int)r.pad[0],
           (int)r.pad[199]);
    printf("%ld\\n", 123456789L);
    return 0;
}
"""

EXPECTED = "a=21 b=42 p0=21 p199=22\n123456789\n"


def test_one_runtime_module_defines_sret_buf():
    """A source-level guard: re-merging the buffer into a code module brings
    the duplicate straight back, and that is a one-line edit away."""
    definers = sorted(p.name for p in RT_DIR.glob("*.mac")
                      if "PUBLIC\t__sret_buf" in p.read_text())
    assert definers == ["rt_sret.mac"], definers


def test_sret_buf_module_holds_nothing_else():
    """rt_sret.mac exists to be pulled on its own.  Anything else PUBLIC in
    it would drag unrelated storage in whenever a struct return is
    unresolved, and would give some other symbol two homes in turn."""
    publics = [line.split("PUBLIC", 1)[1].strip()
               for line in (RT_DIR / "rt_sret.mac").read_text().splitlines()
               if "PUBLIC" in line]
    assert publics == ["__sret_buf"], publics


@pytest.mark.skipif(not _toolchain_available(),
                    reason="um80/ul80 or built libraries not available")
class TestDocumentedLinkLine:
    def _build(self, tmp_path, name, extra_flags=()):
        c_file = tmp_path / (name + ".c")
        c_file.write_text(SOURCE)
        mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
        r = subprocess.run(
            [sys.executable, "-m", "uc80.main", str(c_file),
             "--printf", "long", *extra_flags, "-o", str(mac)],
            capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        subprocess.run(["um80", str(mac), "-o", str(rel)],
                       check=True, capture_output=True, text=True)
        link = subprocess.run(
            ["ul80", str(rel), str(LIB_DIR / "libc.lib"),
             str(LIB_DIR / "runtime.lib"), "-o", str(com)],
            capture_output=True, text=True)
        return link, com

    def test_embedded_runtime_link_reports_nothing(self, tmp_path):
        """The reported case: the default whole-program build, linked the way
        the README documents."""
        link, _ = self._build(tmp_path, "embedded")
        assert "Multiply defined" not in link.stdout + link.stderr, \
            link.stdout + link.stderr

    def test_no_embed_runtime_still_resolves(self, tmp_path):
        """The other side of the split: with nothing embedded the buffer has
        to come out of runtime.lib, so rt_sret must still be reachable."""
        link, _ = self._build(tmp_path, "noembed",
                              extra_flags=("--no-embed-runtime",))
        assert "Multiply defined" not in link.stdout + link.stderr, \
            link.stdout + link.stderr
        assert "Unresolved" not in link.stdout + link.stderr, \
            link.stdout + link.stderr

    @pytest.mark.skipif(not CPMEMU.exists(), reason="cpmemu not built")
    @pytest.mark.parametrize("name,flags", [
        ("embedded", ()),
        ("noembed", ("--no-embed-runtime",)),
    ])
    def test_struct_return_survives_the_split(self, tmp_path, name, flags):
        """Both halves of a 204-byte struct still arrive, so the buffer the
        link settled on is the one the code writes through."""
        _, com = self._build(tmp_path, name, extra_flags=flags)
        run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                             timeout=60)
        out = run.stdout.decode("latin-1").replace("\r", "")
        assert EXPECTED in out, out
