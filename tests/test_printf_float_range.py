"""%f prints the value it was given, at every magnitude a float can hold.

printf's %f built the integer part in H:L:E and then printed it with the
16-bit _prt_dec, which dropped E.  Every |value| >= 65536 came out wrong,
and usually not even as digits, because the leftover low byte was emitted
as a character: 1000000.0 printed as "3906.00///" and -123456.0 emitted a
raw backslash.  Above 2^24 a second path shifted a 16-bit HL left and
printed 0.  sprintf in this same libc always got these right, so a program
could print one value two ways and get two answers.

The integer part now goes through __ftoi and _prt_dec32 like the %ld
handler, and above 2^31 -- where an int32 can no longer hold it -- through
an exact decimal doubling of the mantissa, because a float that large is a
whole number and its decimal form is exact.

Reference values come from glibc printing the same float widened to
double.  A last-digit difference of one unit is expected and is not a bug:
uc80 is 32-bit IEEE 754 and rounds differently, which upstream records as
working as designed.  These cases are chosen to be far larger than one
digit apart.
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


def build_and_run(tmp_path, body, name="prog"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text("#include <stdio.h>\nint main(void){\n%s\nreturn 0;}\n"
                      % body)
    mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac)], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    env = dict(os.environ, PYTHONHASHSEED="0")
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=60, env=env)
    return run.stdout.decode("latin-1").replace("\r", "")


# (literal, expected "%f") -- expected is what glibc prints for the same
# value widened from float to double.
CASES = [
    ("0.0", "0.000000"),
    ("1.0", "1.000000"),
    ("0.5", "0.500000"),
    ("0.125", "0.125000"),
    ("28.0", "28.000000"),
    ("1e-5", "0.000010"),
    ("0.0001234", "0.000123"),
    ("65535.0", "65535.000000"),
    # Everything below here printed garbage before the fix.
    ("65536.0", "65536.000000"),
    ("123456.789", "123456.789062"),
    ("1000000.0", "1000000.000000"),
    ("-123456.0", "-123456.000000"),
    ("16777216.0", "16777216.000000"),
    ("1e7", "10000000.000000"),
    # Above 2^31: an int32 cannot hold the integer part.
    ("1e10", "10000000000.000000"),
    ("2147483648.0", "2147483648.000000"),
    ("4294967296.0", "4294967296.000000"),
    ("1.234e10", "12339999744.000000"),
    ("1e19", "9999999980506447872.000000"),
    ("1e38", "99999996802856924650656260769173209088.000000"),
    ("3.4e38", "339999995214436424907732413799364296704.000000"),
]


class TestValueRange:
    @pytest.mark.parametrize("literal,expected", CASES)
    def test_value_prints_exactly(self, tmp_path, literal, expected):
        out = build_and_run(tmp_path, 'printf("[%%f]\\n", %sf);' % literal)
        assert out == "[%s]\n" % expected

    def test_no_non_digit_bytes_escape(self, tmp_path):
        """The old failure emitted '/', ':', letters and a raw backslash."""
        body = "\n".join(
            'printf("%%f\\n", %sf);' % lit for lit, _ in CASES)
        out = build_and_run(tmp_path, body)
        assert set(out) <= set("0123456789.-\n"), repr(out)

    def test_printf_agrees_with_sprintf(self, tmp_path):
        """One program printed one value two ways and got two answers."""
        out = build_and_run(tmp_path, """
  char b[48];
  printf("%f|", 1000000.0f); sprintf(b, "%f", 1000000.0f); printf("%s\\n", b);
  printf("%f|", 123456.789f); sprintf(b, "%f", 123456.789f); printf("%s\\n", b);
""")
        for line in out.strip().split("\n"):
            direct, viabuf = line.split("|")
            assert direct == viabuf, line


class TestPrecision:
    def test_precision_still_applies_to_large_values(self, tmp_path):
        out = build_and_run(tmp_path, 'printf("[%.3f]\\n", 1e10f);')
        assert out == "[10000000000.000]\n"

    def test_zero_precision_on_a_large_value(self, tmp_path):
        out = build_and_run(tmp_path, 'printf("[%.0f]\\n", 1e10f);')
        assert out.startswith("[10000000000")

    def test_small_values_keep_working(self, tmp_path):
        """|value| < 1 takes a different branch; a signed/unsigned slip in
        the large-value test sent every one of them down the wrong path."""
        out = build_and_run(tmp_path, 'printf("[%f][%f][%f]\\n", 0.5f, 0.25f, 0.1f);')
        assert out == "[0.500000][0.250000][0.100000]\n"


class TestReturnValue:
    """_prt_dec32 wrote straight to __conout, so its digits went uncounted."""

    def test_long_conversion_is_counted(self, tmp_path):
        out = build_and_run(tmp_path, 'printf("n=%d\\n", printf("[%ld]\\n", 1234567L));')
        assert "n=10\n" in out

    def test_float_conversion_is_counted(self, tmp_path):
        out = build_and_run(tmp_path, 'printf("n=%d\\n", printf("[%f]\\n", 1.5f));')
        assert "n=11\n" in out

    def test_large_float_conversion_is_counted(self, tmp_path):
        out = build_and_run(tmp_path, 'printf("n=%d\\n", printf("[%f]\\n", 1e10f));')
        assert "n=21\n" in out


class TestPointAndSign:
    """Two things %f got wrong that %e and %g already got right."""

    def test_zero_precision_emits_no_point(self, tmp_path):
        """C 7.21.6.1: no point after %.0f unless # asks for one."""
        out = build_and_run(tmp_path, 'printf("[%.0f][%#.0f]\\n", 2.0f, 2.0f);')
        assert out == "[2][2.]\n"

    def test_negative_zero_keeps_its_sign(self, tmp_path):
        """The zero test masked the sign bit off before it was checked."""
        out = build_and_run(tmp_path, 'printf("[%f][%f]\\n", -0.0f, 0.0f);')
        assert out == "[-0.000000][0.000000]\n"

    def test_negative_zero_in_every_conversion(self, tmp_path):
        out = build_and_run(tmp_path,
                            'printf("[%f][%e][%g]\\n", -0.0f, -0.0f, -0.0f);')
        assert out == "[-0.000000][-0.000000e+00][-0]\n"

    def test_point_rule_matches_e_and_g(self, tmp_path):
        out = build_and_run(tmp_path,
                            'printf("[%.0f][%.0e][%.0g]\\n", 2.0f, 2.0f, 2.0f);')
        assert out == "[2][2e+00][2]\n"

    def test_small_negative_still_signed(self, tmp_path):
        out = build_and_run(tmp_path, 'printf("[%.1f]\\n", -0.04f);')
        assert out == "[-0.0]\n"
