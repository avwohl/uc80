"""Tests for the %e/%E and %g/%G printf conversions.

Both conversions used to produce an EMPTY field: no handler existed in
src/uc80/lib/lc/, and no format table had an 'e' or 'g' entry.  Worse, a
table miss also leaves the vararg offset alone, so every later conversion
in the same printf call silently read the wrong argument.

lc_prt_float.mac now holds all the float conversions (%f %F %e %E %g %G).
It makes the exact digits with integer arithmetic, so the results here are
exactly what the host prints (tests/test_printf_float_exact.py compares with
Python over random floats).  The end-to-end tests here add the four table
entries to the generated assembly themselves.  register_eg_handlers() is
idempotent, so these tests keep working when codegen emits the entries on its
own.
"""

import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "src" / "uc80" / "lib"
CPMEMU = Path(__file__).resolve().parent.parent.parent / "cpmemu" / "src" / "cpmemu"

# (specifier, handler) pairs the format table needs for %e/%E/%g/%G.
EG_HANDLERS = (
    ("e", "__printf_handle_e"),
    ("E", "__printf_handle_eu"),
    ("g", "__printf_handle_g"),
    ("G", "__printf_handle_gu"),
)


def run_compiler(*args):
    """Run the uc80 compiler with given arguments."""
    return subprocess.run(
        [sys.executable, "-m", "uc80.main", *args],
        capture_output=True,
        text=True,
    )


def register_eg_handlers(code):
    """Add the %e/%E/%g/%G entries to a generated __printf_format_table.

    Idempotent: entries already present are left alone, so this becomes a
    no-op once codegen.py registers the handlers itself.
    """
    lines = code.splitlines()
    have = {m.group(1) for m in
            (re.match(r"\s*db\s+'(.)'\s*$", line) for line in lines) if m}
    missing = [(sp, h) for sp, h in EG_HANDLERS if sp not in have]
    if not missing:
        return code
    out = []
    for line in lines:
        out.append(line)
        if line.strip() == "EXTRN\t__printf_handle_f":
            out += ["\tEXTRN\t%s" % h for _, h in missing]
        elif line.strip() == "dw\t__printf_handle_f":
            for sp, h in missing:
                out += ["\tdb\t'%s'" % sp, "\tdw\t%s" % h]
    return "\n".join(out) + "\n"


class TestBuiltLibrary:
    """build_libs.py concatenates lc/*.mac into the tracked libc.mac."""

    @pytest.mark.skipif(not (LIB_DIR / "libc.mac").exists(),
                        reason="libc.mac not generated")
    def test_handlers_are_in_the_monolithic_libc(self):
        text = (LIB_DIR / "libc.mac").read_text()
        for _, handler in EG_HANDLERS:
            assert handler in text, \
                "%s missing from libc.mac - re-run build_libs.py" % handler


# ---------------------------------------------------------------------------
# End-to-end: compile, register the handlers, assemble, link and run.
# ---------------------------------------------------------------------------

def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80")
            and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


@pytest.mark.skipif(not _toolchain_available(),
                    reason="um80/ul80/cpmemu or built libraries not available")
class TestConversionsOnEmulator:
    """Text inspection is not enough; these must run on a real Z80."""

    def build_and_run(self, tmp_path, body, *extra_args):
        c_file = tmp_path / "prog.c"
        c_file.write_text("#include <stdio.h>\nint main(void){\n%s\nreturn 0;}\n"
                          % body)
        mac = tmp_path / "prog.mac"
        result = run_compiler(str(c_file), "--printf", "all",
                              "-o", str(mac), *extra_args)
        assert result.returncode == 0, result.stderr
        mac.write_text(register_eg_handlers(mac.read_text()))
        rel = tmp_path / "prog.rel"
        com = tmp_path / "prog.com"
        subprocess.run(["um80", str(mac), "-o", str(rel)],
                       check=True, capture_output=True, text=True)
        subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                       check=True, capture_output=True, text=True)
        env = dict(os.environ, PYTHONHASHSEED="0")
        run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                             text=True, timeout=30, env=env)
        return run.stdout.replace("\r", "")

    # -- %e ---------------------------------------------------------------

    def test_e_is_not_empty(self, tmp_path):
        """The reported bug: %e and %g produced nothing at all."""
        out = self.build_and_run(
            tmp_path, 'printf("f=[%f] e=[%e] g=[%g]\\n", 28.0f, 28.0f, 28.0f);')
        assert "f=[28.000000] e=[2.800000e+01] g=[28]" in out

    def test_e_uppercase(self, tmp_path):
        out = self.build_and_run(tmp_path, 'printf("[%E]\\n", 1.5f);')
        assert "[1.500000E+00]" in out

    def test_e_zero_and_negative_zero(self, tmp_path):
        out = self.build_and_run(
            tmp_path, 'printf("[%e][%e]\\n", 0.0f, -0.0f);')
        assert "[0.000000e+00][-0.000000e+00]" in out

    @pytest.mark.parametrize("literal,expected", [
        ("1e1f", "1.000000e+01"), ("1e7f", "1.000000e+07"),
        ("1e11f", "1.000000e+11"), ("1e20f", "1.000000e+20"),
        ("1e30f", "1.000000e+30"), ("1e38f", "1.000000e+38"),
        ("1e-3f", "1.000000e-03"), ("1e-20f", "1.000000e-20"),
        ("1e-30f", "1.000000e-30"), ("1e-37f", "1.000000e-37"),
    ])
    def test_e_powers_of_ten(self, tmp_path, literal, expected):
        """A chained-division scaler drifts a whole decade: 1e20 -> 9.999998e+19.

        Scaling with a single division by the matching table constant makes
        an exact power of ten come out exactly 1.0.
        """
        out = self.build_and_run(tmp_path, 'printf("[%%e]\\n", %s);' % literal)
        assert "[%s]" % expected in out, out

    def test_e_leading_digit_is_never_zero(self, tmp_path):
        """"%e" must be d.ddd; the old scaler could land below 1.0 and emit
        "0.999999904e-04" for 1e-4 at high precision."""
        out = self.build_and_run(tmp_path, "\n".join(
            'printf("[%%.9e]\\n", %s);' % lit
            for lit in ("1e-2f", "1e-4f", "1e-6f", "1e-8f", "1e-10f", "1e-16f")))
        for line in out.splitlines():
            if line.startswith("["):
                assert not line.startswith("[0."), line

    def test_e_precision_zero_has_no_point(self, tmp_path):
        out = self.build_and_run(tmp_path, 'printf("[%.0e]\\n", 28.0f);')
        assert "[3e+01]" in out

    def test_e_alt_flag_keeps_the_point(self, tmp_path):
        """C17 7.21.6.1 p6: '#' forces the point even with no fraction digits."""
        out = self.build_and_run(
            tmp_path, 'printf("[%#.0e][%#.0e]\\n", 28.0f, 0.0f);')
        assert "[3.e+01][0.e+00]" in out

    def test_e_rounding_carries_out(self, tmp_path):
        out = self.build_and_run(tmp_path, 'printf("[%.2e]\\n", 9.999f);')
        assert "[1.00e+01]" in out

    def test_e_exponent_has_at_least_two_digits(self, tmp_path):
        out = self.build_and_run(
            tmp_path, 'printf("[%e][%e]\\n", 1.0f, 5.0f);')
        assert "[1.000000e+00][5.000000e+00]" in out

    def test_e_advances_the_vararg_pointer(self, tmp_path):
        """A table miss used to leave the offset alone and corrupt the rest."""
        out = self.build_and_run(
            tmp_path, 'printf("[%e] next=[%d]\\n", 1.5f, 77);')
        assert "[1.500000e+00] next=[77]" in out

    # -- %g ---------------------------------------------------------------

    @pytest.mark.parametrize("literal,expected", [
        ("28.0f", "28"), ("0.0f", "0"), ("1.0f", "1"), ("0.5f", "0.5"),
        ("100.0f", "100"), ("1.5f", "1.5"), ("-2.5f", "-2.5"),
        ("0.0001f", "0.0001"), ("1e-5f", "1e-05"), ("1.23e-5f", "1.23e-05"),
        ("1e6f", "1e+06"), ("123456.0f", "123456"),
        ("123456789.0f", "1.23457e+08"), ("3.14159f", "3.14159"),
    ])
    def test_g_style_selection(self, tmp_path, literal, expected):
        """C17 7.21.6.1 p8: style e when X < -4 or X >= P, style f otherwise."""
        out = self.build_and_run(tmp_path, 'printf("[%%g]\\n", %s);' % literal)
        assert "[%s]" % expected in out, out

    def test_g_beats_f_on_large_integers(self, tmp_path):
        """%g takes its digits from the scaled mantissa, never from the
        16-bit _prt_dec that makes %f garbage above 65535."""
        out = self.build_and_run(
            tmp_path, 'printf("[%g][%g]\\n", 123456.0f, 1e7f);')
        assert "[123456][1e+07]" in out

    def test_g_uppercase(self, tmp_path):
        out = self.build_and_run(tmp_path, 'printf("[%G]\\n", 1.5e-7f);')
        assert "[1.5E-07]" in out

    def test_g_explicit_precision(self, tmp_path):
        out = self.build_and_run(
            tmp_path, 'printf("[%.3g][%.1g][%.7g]\\n", 3.14159f, 0.0f, 1.5f);')
        assert "[3.14][0][1.5]" in out

    def test_g_alt_keeps_trailing_zeros(self, tmp_path):
        out = self.build_and_run(tmp_path, 'printf("[%#g]\\n", 28.0f);')
        assert "[28.0000]" in out

    def test_g_alt_keeps_the_point_with_no_fraction_digits(self, tmp_path):
        """P-1-X is zero here, so the point is never armed; '#' still
        requires it.  This printed "100000" before."""
        out = self.build_and_run(
            tmp_path, 'printf("[%#g][%#g]\\n", 100000.0f, 999999.0f);')
        assert "[100000.][999999.]" in out

    def test_g_alt_zero_keeps_its_precision(self, tmp_path):
        """Zero must go through the ordinary style-f path; a "print 0 and
        stop" special case gets %#g wrong."""
        out = self.build_and_run(
            tmp_path, 'printf("[%#g][%#.3g]\\n", 0.0f, 1.0f);')
        assert "[0.00000][1.00]" in out

    def test_g_advances_the_vararg_pointer(self, tmp_path):
        out = self.build_and_run(
            tmp_path, 'printf("[%g] next=[%d]\\n", 1.5f, 77);')
        assert "[1.5] next=[77]" in out

    def test_mixed_conversions_stay_aligned(self, tmp_path):
        out = self.build_and_run(
            tmp_path,
            'printf("[%e|%g|%d|%s|%f]\\n", 2.5f, 2.5f, 42, "ok", 2.5f);')
        assert "[2.500000e+00|2.5|42|ok|2.500000]" in out

    # -- special values ---------------------------------------------------

    def test_infinity_and_nan(self, tmp_path):
        body = """
static float mk(unsigned long b){ float f; unsigned long *p=(unsigned long*)&f;
  *p=b; return f; }
"""
        c_file = tmp_path / "spec.c"
        c_file.write_text(
            "#include <stdio.h>\n"
            "static float mk(unsigned long b){ float f;"
            " unsigned long *p=(unsigned long*)&f; *p=b; return f; }\n"
            "int main(void){\n"
            ' printf("[%e][%E][%g][%G]\\n", mk(0x7F800000UL), mk(0x7F800000UL),'
            " mk(0xFF800000UL), mk(0xFF800000UL));\n"
            ' printf("[%e][%E][%g][%G]\\n", mk(0x7FC00000UL), mk(0x7FC00000UL),'
            " mk(0xFFC00000UL), mk(0xFFC00000UL));\n"
            ' printf("after=[%d]\\n", 55);\n return 0;}\n')
        mac = tmp_path / "spec.mac"
        result = run_compiler(str(c_file), "--printf", "all", "-o", str(mac))
        assert result.returncode == 0, result.stderr
        mac.write_text(register_eg_handlers(mac.read_text()))
        rel, com = tmp_path / "spec.rel", tmp_path / "spec.com"
        subprocess.run(["um80", str(mac), "-o", str(rel)],
                       check=True, capture_output=True, text=True)
        subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                       check=True, capture_output=True, text=True)
        out = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                             text=True, timeout=30).stdout.replace("\r", "")
        assert "[inf][INF][-inf][-INF]" in out
        assert "[nan][NAN][-nan][-NAN]" in out
        assert "after=[55]" in out

    # -- no regression in %f ----------------------------------------------

    def test_f_conversion_is_unchanged(self, tmp_path):
        """The new modules share only _printf_putc, the flag bytes and
        __ftmp with _prt_float, so %f cannot regress."""
        out = self.build_and_run(
            tmp_path,
            'printf("[%f][%f][%f][%f]\\n", 28.0f, 3.14159f, 0.5f, 1.0f);')
        assert "[28.000000][3.141590][0.500000][1.000000]" in out
