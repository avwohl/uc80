"""Console line endings: libc translates '\\n' to CR LF, --no-crlf opts out.

A real CP/M console treats LF as "cursor down" only, so a bare LF makes
output stair-step.  libc used to be internally split about this: puts()
hard-coded CR LF while printf/putchar/fputs/fprintf emitted a bare LF, so
one program printed two different line terminators depending on which
stdio call it used.  There is now a single choke point, lc/lc_conout.mac,
and every console writer in lc/ goes through it.

The choke-point invariant is load-bearing, not cosmetic: __conout
suppresses the inserted CR when the previous character written was
already a CR, and that state is only correct if nothing bypasses it.
TestChokePointInvariant is the CI guard for that.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from uc_core.frontend import parse as _frontend_parse
from uc80.codegen import CodeGenerator, generate

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
LC_DIR = LIB_DIR / "lc"
CONOUT_MAC = LC_DIR / "lc_conout.mac"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"

# The console writers that must route through __conout.  lc_stdlib.mac is
# deliberately absent: abort() uses BDOS 9 with a '$'-terminated string and
# is a terminal call (see the module note in lc_conout.mac).
CONSOLE_MODULES = [
    "lc_putchar.mac",
    "lc_puts.mac",
    "lc_printf_core.mac",
    "lc_file.mac",
    "lc_prt_dec32.mac",
    "lc_prt_dec64.mac",
    "lc_prt_hex.mac",
    "lc_assert.mac",
]

# `LD C,2` / `LD C,CONOUT` immediately before a BDOS call is a console
# write.  Matching the LD alone is enough and is what a reviewer greps for.
_BDOS_CONOUT = re.compile(r"^\s+LD\s+C,(?:CONOUT|2)\s*(?:;.*)?$", re.MULTILINE)


def _parse(source: str):
    return _frontend_parse(source, "<test>")


def _gen(source: str, **kwargs) -> str:
    cg = CodeGenerator("test", **kwargs)
    return cg.generate(_parse(source))


class TestChokePointInvariant:
    """Nothing in lc/ may call BDOS 2 except lc_conout.mac itself."""

    def test_only_lc_conout_writes_to_bdos_2(self):
        offenders = {}
        for path in sorted(LC_DIR.glob("*.mac")):
            hits = _BDOS_CONOUT.findall(path.read_text())
            if hits:
                offenders[path.name] = len(hits)
        assert offenders == {"lc_conout.mac": 2}, (
            "console output must go through __conout; found direct BDOS 2 "
            "calls in %r" % offenders)

    @pytest.mark.parametrize("module", CONSOLE_MODULES)
    def test_console_module_declares_the_extern(self, module):
        assert "\tEXTRN\t__conout\n" in (LC_DIR / module).read_text()

    @pytest.mark.parametrize("module", CONSOLE_MODULES)
    def test_console_module_calls_the_choke_point(self, module):
        assert "CALL\t__conout" in (LC_DIR / module).read_text()


class TestConoutModule:
    """Shape of lc_conout.mac, so a refactor cannot quietly change policy."""

    def test_exports_the_choke_point(self):
        assert "\tPUBLIC\t__conout\n" in CONOUT_MAC.read_text()

    def test_exports_the_runtime_flag(self):
        """Three underscores: uc80 prefixes the C name __crlf_mode with one."""
        assert "\tPUBLIC\t___crlf_mode\n" in CONOUT_MAC.read_text()

    def test_flag_defaults_to_translating(self):
        assert re.search(r"^___crlf_mode:\s+DB\s+1\b", CONOUT_MAC.read_text(),
                         re.MULTILINE)

    def test_flag_lives_in_initialised_data_not_bss(self):
        """crt0 zeroes COMMON; a DB 1 default only survives in DSEG."""
        src = CONOUT_MAC.read_text()
        assert src.index("\tDSEG") < src.index("___crlf_mode:")
        assert "COMMON" not in src

    def test_translation_is_conditional_on_the_flag(self):
        assert "LD\tA,(___crlf_mode)" in CONOUT_MAC.read_text()

    def test_suppresses_a_second_cr(self):
        """printf("\\r\\n") must not become "\\r\\r\\n"."""
        src = CONOUT_MAC.read_text()
        assert "__conout_last" in src
        assert "CP\t0DH" in src


class TestNoHardCodedLineEndings:
    """The old inconsistency: three routines hard-coded CR LF, four did not."""

    def test_puts_no_longer_hard_codes_cr(self):
        src = (LC_DIR / "lc_puts.mac").read_text()
        assert "LD\tE,0DH" not in src
        assert "LD\tE,0AH\n\tCALL\t__conout" in src

    def test_assert_no_longer_hard_codes_cr(self):
        assert "LD\tE,0DH" not in (LC_DIR / "lc_assert.mac").read_text()

    def test_perror_no_longer_hard_codes_cr(self):
        """_perror used the decimal spellings LD E,13 / LD E,10."""
        src = (LC_DIR / "lc_file.mac").read_text()
        assert "\tLD\tE,13\n" not in src


class TestFileWritesStayRaw:
    """A "wb" stream must not get CR injected -- nor a "w" one, today."""

    def test_only_the_console_branch_of_fputc_is_routed(self):
        src = (LC_DIR / "lc_file.mac").read_text()
        console, _, rest = src.partition("_fputc_file:")
        # The console branch above _fputc_file calls __conout...
        assert "CALL\t__conout" in console
        # ...and the buffered-file path below it, up to the next function,
        # never does.
        file_branch = rest.split("; ====")[0]
        assert "__conout" not in file_branch


class TestLibcMacInSync:
    """libc.mac is a tracked build artefact; it must match lc/*.mac."""

    def test_libc_mac_contains_the_choke_point(self):
        assert "\tPUBLIC\t__conout\n" in (LIB_DIR / "libc.mac").read_text()

    def test_libc_mac_has_no_stale_bdos_2_writers(self):
        # concat_modules() emits every lc/ module verbatim, so the same
        # invariant has to hold in the monolithic file.
        assert len(_BDOS_CONOUT.findall((LIB_DIR / "libc.mac").read_text())) == 2

    def test_libc_mac_lists_lc_conout(self):
        assert "; lc_conout.mac" in (LIB_DIR / "libc.mac").read_text()


class TestStdioHeader:
    def test_declares_the_runtime_flag(self):
        assert "extern char __crlf_mode;" in (
            LIB_DIR / "include" / "stdio.h").read_text()


class TestCodegenFlag:
    """--no-crlf is a four-byte store at the top of main()."""

    SRC = "int main(void) { return 0; }"

    def test_default_emits_nothing(self):
        assert "___crlf_mode" not in _gen(self.SRC)

    def test_no_crlf_emits_the_store(self):
        code = _gen(self.SRC, crlf_console=False)
        assert "extrn\t___crlf_mode" in code
        assert "xor\tA" in code
        assert "ld\t(___crlf_mode),A" in code

    def test_store_is_inside_main_after_the_prologue(self):
        code = _gen("int main(void) { return 0; }", crlf_console=False)
        assert code.index("_main:") < code.index("ld\t(___crlf_mode),A")
        assert code.index("add\tIX,SP") < code.index("ld\t(___crlf_mode),A")

    def test_store_runs_before_the_body(self):
        code = _gen("int main(void) { return 42; }", crlf_console=False)
        assert code.index("ld\t(___crlf_mode),A") < code.index("ld\tHL,42")

    def test_only_main_gets_the_store(self):
        code = _gen("void f(void) { } int main(void) { f(); return 0; }",
                    crlf_console=False)
        assert code.count("ld\t(___crlf_mode),A") == 1
        assert code.index("_main:") < code.index("ld\t(___crlf_mode),A")

    def test_translation_unit_without_main_gets_nothing(self):
        """Documented limitation: only the TU defining main() honours it."""
        code = _gen("int helper(int x) { return x + 1; }", crlf_console=False)
        assert "___crlf_mode" not in code

    def test_module_level_generate_helper_forwards_the_flag(self):
        assert "___crlf_mode" in generate(_parse(self.SRC), crlf_console=False)
        assert "___crlf_mode" not in generate(_parse(self.SRC))


class TestCli:
    def test_flag_is_advertised(self):
        r = subprocess.run([sys.executable, "-m", "uc80.main", "--help"],
                           capture_output=True, text=True, cwd=str(REPO))
        assert "--no-crlf" in r.stdout

    def test_default_compile_has_no_store(self, tmp_path):
        c = tmp_path / "p.c"
        c.write_text("int main(void){return 0;}\n")
        mac = tmp_path / "p.mac"
        r = subprocess.run([sys.executable, "-m", "uc80.main", str(c),
                            "-o", str(mac)], capture_output=True, text=True,
                           cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        assert "___crlf_mode" not in mac.read_text()

    def test_no_crlf_compile_has_the_store(self, tmp_path):
        c = tmp_path / "p.c"
        c.write_text("int main(void){return 0;}\n")
        mac = tmp_path / "p.mac"
        r = subprocess.run([sys.executable, "-m", "uc80.main", "--no-crlf",
                            str(c), "-o", str(mac)], capture_output=True,
                           text=True, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        assert "ld\t(___crlf_mode),A" in mac.read_text()


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


@pytest.mark.skipif(not _toolchain_available(),
                    reason="um80/ul80/cpmemu or built libraries not available")
class TestOnEmulator:
    """Text inspection cannot see a line ending; these run on a real Z80."""

    def build_and_run(self, tmp_path, body, *extra_args, prelude=""):
        c_file = tmp_path / "prog.c"
        c_file.write_text("#include <stdio.h>\n%s\nint main(void){\n%s\n"
                          "return 0;}\n" % (prelude, body))
        mac, rel, com = (tmp_path / ("prog" + e) for e in (".mac", ".rel", ".com"))
        r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                            "-o", str(mac), *extra_args],
                           capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        subprocess.run(["um80", str(mac), "-o", str(rel)],
                       check=True, capture_output=True, text=True)
        link = ["ul80", str(rel)]
        if "--no-whole-program" in extra_args:
            link = ["ul80", str(LIB_DIR / "crt0.rel"), str(rel)]
        link += [str(LIB_DIR / "libc.lib"), str(LIB_DIR / "runtime.lib"),
                 "-o", str(com)]
        subprocess.run(link, check=True, capture_output=True, text=True)
        env = dict(os.environ, PYTHONHASHSEED="0")
        # NOT text=True: the whole point is the exact bytes on the wire.
        run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                             timeout=30, env=env, cwd=str(tmp_path))
        return run.stdout

    def test_every_stdio_path_emits_crlf(self, tmp_path):
        """The reported bug, and the puts-vs-printf inconsistency with it."""
        out = self.build_and_run(tmp_path, r'''
            printf("printf %d\n", 1);
            puts("puts");
            putchar('c'); putchar('\n');
            fputs("fputs\n", stdout);
            fprintf(stdout, "fprintf\n");
        ''')
        assert out == (b"printf 1\r\nputs\r\nc\r\nfputs\r\nfprintf\r\n")

    def test_no_crlf_restores_bare_lf_everywhere(self, tmp_path):
        out = self.build_and_run(tmp_path, r'''
            printf("printf %d\n", 1);
            puts("puts");
            putchar('c'); putchar('\n');
            fputs("fputs\n", stdout);
        ''', "--no-crlf")
        assert out == b"printf 1\nputs\nc\nfputs\n"
        assert b"\r" not in out

    def test_no_crlf_in_separate_compilation(self, tmp_path):
        """The store must work with the prebuilt crt0.rel too, not just the
        embedded one -- that is why it is not injected into crt0."""
        out = self.build_and_run(tmp_path, 'printf("x\\n");',
                                 "--no-crlf", "--no-whole-program")
        assert out == b"x\n"

    def test_default_in_separate_compilation(self, tmp_path):
        out = self.build_and_run(tmp_path, 'printf("x\\n");',
                                 "--no-whole-program")
        assert out == b"x\r\n"

    def test_existing_crlf_is_not_doubled(self, tmp_path):
        out = self.build_and_run(tmp_path, r'printf("a\r\nb\n");')
        assert out == b"a\r\nb\r\n"

    def test_bare_cr_still_passes_through(self, tmp_path):
        """A progress-bar redraw must not gain an LF."""
        out = self.build_and_run(tmp_path, r'printf("bar[\rredraw\n");')
        assert out == b"bar[\rredraw\r\n"

    def test_runtime_flag_is_settable_from_c(self, tmp_path):
        out = self.build_and_run(tmp_path, r'''
            printf("on\n");
            __crlf_mode = 0;
            printf("off\n"); puts("off2");
            __crlf_mode = 1;
            printf("on2\n");
        ''')
        assert out == b"on\r\noff\noff2\non2\r\n"

    def test_digit_emitters_keep_the_state_accurate(self, tmp_path):
        """vprintf's %d/%x/%ld reach lc_prt_dec32/dec64/hex directly.  If
        those bypassed __conout the CR suppression would misfire."""
        out = self.build_and_run(
            tmp_path,
            'vp("%d %x %ld\\n", 3, 255, 100000L);'
            'printf("%llu\\n", 5ULL);',
            prelude="#include <stdarg.h>\n"
                    "static void vp(const char *f, ...){ va_list a;"
                    " va_start(a,f); vprintf(f,a); va_end(a); }")
        # (%llu goes through printf, not vp: vprintf drops %ll entirely --
        # a pre-existing bug, identical on unpatched uc80.)
        assert out == b"3 ff 100000\r\n5\r\n"

    def test_perror_follows_the_policy(self, tmp_path):
        out = self.build_and_run(tmp_path, 'perror("oops");')
        assert out.endswith(b"\r\n")
        assert out.count(b"\r") == 1

    def test_file_streams_are_never_translated(self, tmp_path):
        """Both "w" and "wb" must write the bytes the program handed us."""
        out = self.build_and_run(tmp_path, r'''
            FILE *f = fopen("OUTB.DAT", "wb");
            fwrite("ab\ncd\n", 1, 6, f); fclose(f);
            FILE *g = fopen("OUTT.DAT", "w");
            fputs("ef\ngh\n", g); fputc('\n', g); fclose(g);
            printf("done\n");
        ''')
        assert out == b"done\r\n"
        assert (tmp_path / "outb.dat").read_bytes() == b"ab\ncd\n"
        assert (tmp_path / "outt.dat").read_bytes() == b"ef\ngh\n\n"
