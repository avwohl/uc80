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

The buffer used to be where every struct return landed, and the return copy
was an LDIR of the struct's full size into a DS 256 buffer with nothing
bounding it at run time.  The destination is now a slot in the caller's own
frame, so the size limit is gone and __sret_buf is only reached by a call
whose return type could not be resolved when the slots were laid out --
which is why it still has to be defined exactly once.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from uc80.codegen import SRET_BUF_SIZE

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


def test_size_constant_matches_the_module():
    """codegen bounds the return copy against SRET_BUF_SIZE, but rt_sret.mac
    is what actually reserves the bytes.  If the two drift apart the check
    stops matching the buffer it is protecting, in whichever direction."""
    text = (RT_DIR / "rt_sret.mac").read_text()
    m = re.search(r"^__sret_buf:\s*DS\s+(\d+)", text, re.MULTILINE)
    assert m, text
    assert int(m.group(1)) == SRET_BUF_SIZE


@pytest.mark.skipif(not shutil.which("um80"), reason="um80 not available")
class TestAnySizeStructReturn:
    """The destination is the caller's business now, so an aggregate return
    is not bounded by __sret_buf.  These used to be the tests for the
    diagnostic that refused anything over 256 bytes."""

    def _compile(self, tmp_path, source, name="big"):
        c_file = tmp_path / (name + ".c")
        c_file.write_text(source)
        return subprocess.run(
            [sys.executable, "-m", "uc80.main", str(c_file),
             "-o", str(tmp_path / (name + ".mac"))],
            capture_output=True, text=True, cwd=str(REPO))

    def _returning(self, nbytes, aggregate="struct"):
        return ("%s s { char p[%d]; };\n"
                "%s s mk(void) { %s s v; v.p[0] = 1; return v; }\n"
                "int main(void) { return mk().p[0]; }\n"
                % (aggregate, nbytes, aggregate, aggregate))

    @pytest.mark.parametrize("nbytes", [2, 3, SRET_BUF_SIZE,
                                        SRET_BUF_SIZE + 1, 1000])
    def test_every_size_compiles(self, tmp_path, nbytes):
        r = self._compile(tmp_path, self._returning(nbytes))
        assert r.returncode == 0, r.stderr

    def test_a_union_is_no_different(self, tmp_path):
        r = self._compile(tmp_path, self._returning(1000, aggregate="union"))
        assert r.returncode == 0, r.stderr

    def test_nothing_references_the_buffer(self, tmp_path):
        """A plain struct return used to be the one thing that pulled
        __sret_buf into a program.  It no longer needs it, so the 256 bytes
        stop being reserved."""
        self._compile(tmp_path, self._returning(20))
        assert "__sret_buf" not in (tmp_path / "big.mac").read_text()

    def test_a_big_struct_is_fine_if_it_is_not_returned(self, tmp_path):
        """Passing one by pointer, which is what the old diagnostic told
        the user to do, still works at any size."""
        r = self._compile(tmp_path, """
struct s { char p[1000]; };
void fill(struct s *v) { v->p[0] = 1; }
int main(void) { struct s v; fill(&v); return v.p[0]; }
""")
        assert r.returncode == 0, r.stderr


def test_the_libc_generator_does_not_define_it_either():
    """split_libc.py writes lc_data.mac.  It carried its own 64-byte
    __sret_buf, so regenerating the library reintroduced the duplicate --
    at a quarter of the size codegen bounds the fallback copy against, and
    L80 keeps whichever definition it links first."""
    text = (LIB_DIR / "split_libc.py").read_text()
    assert "__sret_buf:" not in text, "the generator defines the buffer again"
