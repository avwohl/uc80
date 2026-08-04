"""Run-time behaviour on a printf conversion with no handler (D7).

Before this change a table miss produced NOTHING and left the vararg offset
alone, so a single unsupported conversion silently corrupted every later
field in the same call.  lc_printf_core.mac now echoes the whole conversion
specification verbatim, so the failure is visible.  It still cannot resync
the varargs -- there is no portable way to know an unknown conversion's
argument size -- which is asserted below so nobody assumes otherwise.

The complementary compile-time warning is in tests/test_printf_warnings.py.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CORE_MAC = LIB_DIR / "lc" / "lc_printf_core.mac"
L_MAC = LIB_DIR / "lc" / "lc_printf_l.mac"
ALL_MAC = LIB_DIR / "lc" / "lc_printf_all.mac"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"


class TestRuntimeEchoSource:
    """Shape checks on the assembly, so a refactor cannot quietly drop it."""

    def test_core_records_the_spec_start(self):
        assert "LD\t(_printf_fmtstart),HL" in CORE_MAC.read_text()

    def test_core_defines_the_state_byte(self):
        assert "_printf_fmtstart:" in CORE_MAC.read_text()

    def test_echo_entry_point_is_public(self):
        assert "\tPUBLIC\t_printf_unk_echo" in CORE_MAC.read_text()

    def test_long_handler_misses_reach_the_echo(self):
        """%ls used to vanish silently; the 'l' handler has its own table."""
        src = L_MAC.read_text()
        assert "\tEXTRN\t_printf_unk_echo" in src
        assert src.count("JP\t_printf_unk_echo") == 2

    def test_limitation_is_documented_at_the_code(self):
        """The echo does not resync the varargs -- say so where it lives."""
        src = CORE_MAC.read_text()
        assert "does NOT resync the varargs" in src


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


@pytest.mark.skipif(not _toolchain_available(),
                    reason="um80/ul80/cpmemu or built libraries not available")
class TestRuntimeEchoOnEmulator:
    """Text inspection is not enough; these run on a real Z80."""

    def build_and_run(self, tmp_path, body, *extra_args):
        c_file = tmp_path / "prog.c"
        c_file.write_text("#include <stdio.h>\nint main(void){\n%s\nreturn 0;}\n"
                          % body)
        mac, rel, com = (tmp_path / ("prog" + e) for e in (".mac", ".rel", ".com"))
        r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                            "-o", str(mac), *extra_args],
                           capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        subprocess.run(["um80", str(mac), "-o", str(rel)],
                       check=True, capture_output=True, text=True)
        subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                       check=True, capture_output=True, text=True)
        env = dict(os.environ, PYTHONHASHSEED="0")
        run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                            text=True, timeout=30, env=env)
        return run.stdout.replace("\r", "")

    def test_bare_unknown_conversion_is_echoed(self, tmp_path):
        out = self.build_and_run(tmp_path, 'printf("A[%q]|\\n", 1);')
        assert "A[%q]|" in out

    def test_flags_and_precision_are_echoed_too(self, tmp_path):
        out = self.build_and_run(tmp_path, 'printf("B[%.3q][%-5w]|\\n", 1, 2);')
        assert "B[%.3q][%-5w]|" in out

    def test_long_table_miss_is_echoed(self, tmp_path):
        """%ls goes through the 'l' handler's own table, which also missed."""
        out = self.build_and_run(tmp_path, 'printf("C[%ls]|\\n", "x");')
        assert "C[%ls]|" in out

    def test_echo_does_not_resync_the_varargs(self, tmp_path):
        """Documented limitation, asserted so nobody assumes otherwise."""
        out = self.build_and_run(tmp_path, 'printf("E[%d][%q][%d]|\\n", 1, 2, 3);')
        assert "E[1][%q][2]|" in out

    def test_feature_gap_is_echoed(self, tmp_path):
        out = self.build_and_run(
            tmp_path, 'printf("d=[%d] s=[%s] f=[%f]\\n", 42, "hi", 1.5);',
            "--printf", "int")
        assert "d=[42] s=[hi] f=[%f]" in out

    def test_good_formats_are_untouched(self, tmp_path):
        out = self.build_and_run(
            tmp_path, 'printf("[%d][%s][%x][%ld]\\n", 42, "hi", 255, 100000L);')
        assert "[42][hi][ff][100000]" in out


@pytest.mark.skipif(not shutil.which("um80"), reason="um80 not available")
class TestCoreStillAssembles:
    def test_core_assembles(self, tmp_path):
        out = tmp_path / "core.rel"
        r = subprocess.run(["um80", str(CORE_MAC), "-o", str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def test_l_handler_assembles(self, tmp_path):
        out = tmp_path / "l.rel"
        r = subprocess.run(["um80", str(L_MAC), "-o", str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def test_libc_mac_is_in_sync(self):
        """build_libs.py regenerates the tracked libc.mac by concatenation."""
        assert "_printf_unk_echo" in (LIB_DIR / "libc.mac").read_text(), \
            "libc.mac is stale - re-run build_libs.py"
