"""Every printf-family entry point formats the same way.

printf, sprintf, snprintf, fprintf, vprintf, vfprintf and vsprintf each
used to carry their own conversion chain.  A conversion added to printf's
dispatch table reached printf and nothing else, so the bug that started
this -- %e and %g printing an empty field and, because a miss does not
advance the vararg offset, desyncing every later conversion in the same
call -- was fixed in one of five entry points and left standing in four:

    printf  =[1][2.500000][2.500000e+00][2.5][2.500000][9]
    sprintf =[1][2.500000][][][][0]
    snprintf=[1][][][][][0]        <- no float conversion at all
    fprintf =[1][][][][][0]
    vprintf =[1][][][][][0]

They now share lc_printf_core's _printf_engine, which takes the frame,
the format string, the offset of the first vararg and a sink.  The tests
below are mostly differential: whatever printf does, the others must do,
so a conversion added later cannot quietly reach only one of them.
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


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or built libraries not available")

PRELUDE = """#include <stdio.h>
#include <stdarg.h>
char buf[128];
void vp(const char *f, ...){ va_list a; va_start(a,f); vprintf(f,a); va_end(a); }
void vs(char *o, const char *f, ...){ va_list a; va_start(a,f); vsprintf(o,f,a); va_end(a); }
void vfp(FILE *s, const char *f, ...){ va_list a; va_start(a,f); vfprintf(s,f,a); va_end(a); }
"""


def build_and_run(tmp_path, body, *extra_args, name="prog"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text(PRELUDE + "int main(void){\n%s\nreturn 0;}\n" % body)
    mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
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
    run = subprocess.run([str(CPMEMU), str(com)], cwd=str(tmp_path),
                         capture_output=True, timeout=60, env=env)
    return run.stdout.decode("latin-1").replace("\r", "")


# Every conversion the dispatch table knows, in one format string.
FMT = "[%d][%s][%c][%x][%u][%ld][%f][%e][%g][%F][%d]"
ARGS = '1,"s",\'c\',255,7,100000L,2.5,2.5,2.5,2.5,9'
EXPECT = "[1][s][c][ff][7][100000][2.500000][2.500000e+00][2.5][2.500000][9]"


# Each entry point running the identical format string and arguments.
MATRIX = (
    'printf("printf  =' + FMT + '\\n", ' + ARGS + ');\n'
    'sprintf(buf, "sprintf =' + FMT + '", ' + ARGS + '); puts(buf);\n'
    'snprintf(buf, sizeof buf, "snprintf=' + FMT + '", ' + ARGS + '); puts(buf);\n'
    'fprintf(stdout, "fprintf =' + FMT + '\\n", ' + ARGS + ');\n'
    'vp("vprintf =' + FMT + '\\n", ' + ARGS + ');\n'
    'vs(buf, "vsprintf=' + FMT + '", ' + ARGS + '); puts(buf);\n'
    'vfp(stdout, "vfprintf=' + FMT + '\\n", ' + ARGS + ');\n'
)

ENTRY_POINTS = ["printf", "sprintf", "snprintf", "fprintf",
                "vprintf", "vsprintf", "vfprintf"]


class TestAllEntryPointsAgree:
    """The differential check: nothing may format differently from printf."""

    @pytest.fixture(scope="class")
    def matrix(self, tmp_path_factory):
        out = build_and_run(tmp_path_factory.mktemp("matrix"), MATRIX)
        rows = {}
        for line in out.split("\n"):
            if not line:
                continue
            name, _, got = line.partition("=")
            rows[name.strip()] = got
        return rows

    def test_every_entry_point_reported(self, matrix):
        assert sorted(matrix) == sorted(ENTRY_POINTS)

    @pytest.mark.parametrize("entry", ENTRY_POINTS)
    def test_entry_point_matches_printf(self, matrix, entry):
        assert matrix[entry] == EXPECT


class TestSnprintfBounds:
    """C 7.21.6.5: at most size-1 characters plus a NUL; return the length
    the whole conversion would have had."""

    def test_truncates_and_returns_full_length(self, tmp_path):
        out = build_and_run(tmp_path, """
  int n = snprintf(buf, 5, "abcdefgh");
  printf("n=%d buf=[%s]\\n", n, buf);""")
        assert out == "n=8 buf=[abcd]\n"

    def test_size_one_writes_only_the_nul(self, tmp_path):
        out = build_and_run(tmp_path, """
  int n = snprintf(buf, 1, "abcdefgh");
  printf("n=%d buf=[%s]\\n", n, buf);""")
        assert out == "n=8 buf=[]\n"

    def test_size_zero_does_not_touch_the_buffer(self, tmp_path):
        out = build_and_run(tmp_path, """
  buf[0] = 'Z';
  int n = snprintf(buf, 0, "abcdefgh");
  printf("n=%d first=%c\\n", n, buf[0]);""")
        assert out == "n=8 first=Z\n"

    def test_exact_fit(self, tmp_path):
        out = build_and_run(tmp_path, """
  int n = snprintf(buf, 9, "abcdefgh");
  printf("n=%d buf=[%s]\\n", n, buf);""")
        assert out == "n=8 buf=[abcdefgh]\n"

    def test_truncation_mid_conversion(self, tmp_path):
        """The cut may land inside a conversion, not just between them."""
        out = build_and_run(tmp_path, """
  int n = snprintf(buf, 6, "ab%f", 1.5);
  printf("n=%d buf=[%s]\\n", n, buf);""")
        assert out == "n=10 buf=[ab1.5]\n"


class TestReturnValues:
    def test_sprintf_counts_bytes_without_the_nul(self, tmp_path):
        out = build_and_run(tmp_path, """
  printf("n=%d\\n", sprintf(buf, "abc%d", 42));""")
        assert out == "n=5\n"

    def test_fprintf_counts_bytes(self, tmp_path):
        out = build_and_run(tmp_path, """
  FILE *f = fopen("o.txt", "w");
  printf("n=%d\\n", fprintf(f, "file %d %f\\n", 3, 2.5));
  fclose(f);""")
        assert out == "n=16\n"

    def test_vsprintf_terminates_and_counts(self, tmp_path):
        out = build_and_run(tmp_path, """
  vs(buf, "v=%d/%f", 5, 1.5);
  printf("buf=[%s]\\n", buf);""")
        assert out == "buf=[v=5/1.500000]\n"


class TestSinkIsRestored:
    """A buffer-writing call must not leave the console pointing at it."""

    def test_printf_after_sprintf(self, tmp_path):
        out = build_and_run(tmp_path, """
  sprintf(buf, "hidden");
  printf("visible=[%s]\\n", buf);""")
        assert out == "visible=[hidden]\n"

    def test_sprintf_nested_in_a_printf_argument(self, tmp_path):
        out = build_and_run(tmp_path, """
  printf("n=%d buf=[%s]\\n", sprintf(buf, "%f", 2.5), buf);""")
        assert out == "n=8 buf=[2.500000]\n"

    def test_fprintf_to_a_file_does_not_steal_the_console(self, tmp_path):
        out = build_and_run(tmp_path, """
  FILE *f = fopen("o.txt", "w");
  fprintf(f, "to the file\\n");
  fclose(f);
  printf("to the console\\n");""")
        assert out == "to the console\n"


class TestFileStreamsAreNotTranslated:
    """CR LF is console policy; a FILE keeps the bytes it was given."""

    def test_fprintf_newline_stays_one_byte(self, tmp_path):
        build_and_run(tmp_path, """
  FILE *f = fopen("o.txt", "w");
  fprintf(f, "a\\nb\\n");
  fclose(f);""")
        assert (tmp_path / "o.txt").read_bytes() == b"a\nb\n"
