"""printf flags, field width and precision, checked against the host printf.

Bug family: the integer conversions ignored the precision (%.4d), the '-'
flag (%-5d), the width and flags of every long form (%8ld, %08lx), the
width of %s, and %*d was not supported.  Every integer handler now ends in
one shared routine, _printf_numout (lc_printf_core.mac).

Each test builds one C program of printf calls, compiles the same source with
the host C compiler, and compares the two outputs line by line.  int is 16 bit
on uc80, so the int arguments stay inside 16 bits.  The tests that run the
program need um80, ul80, cpmemu and the built libraries; the host compiler
supplies the expected text.
"""

import itertools
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"
HOST_CC = shutil.which("cc")


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and HOST_CC and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu, a host cc or the built libraries are missing")


def build_and_run(tmp_path, source, *extra_args):
    c_file = tmp_path / "prog.c"
    c_file.write_text(source)
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
                         text=True, timeout=120, env=env)
    return run.stdout.replace("\r", "")


def host_run(tmp_path, source):
    c_file = tmp_path / "host.c"
    c_file.write_text(source)
    exe = tmp_path / "host"
    subprocess.run([HOST_CC, "-w", "-o", str(exe), str(c_file)],
                   check=True, capture_output=True, text=True)
    return subprocess.run([str(exe)], capture_output=True, text=True,
                          check=True).stdout


def program(cases):
    """cases: list of (format, argument source text or '')."""
    lines = ["#include <stdio.h>", "int main(void) {"]
    for fmt, args in cases:
        lines.append('  printf("[%s]\\n"%s);' % (fmt, ", " + args if args else ""))
    lines += ["  return 0;", "}", ""]
    return "\n".join(lines)


def check(tmp_path, cases):
    src = program(cases)
    expect = host_run(tmp_path, src).splitlines()
    got = build_and_run(tmp_path, src).splitlines()
    assert len(got) == len(expect), "got %d lines, expected %d" % (len(got), len(expect))
    bad = ["%-22s want %r got %r" % (c[0] + " " + c[1], e, g)
           for c, e, g in zip(cases, expect, got) if e != g]
    assert not bad, "\n".join(bad[:25])


INT_VALUES = ["0", "1", "-1", "42", "-42", "255", "12345", "-12345", "32767", "-32767-1"]
UINT_VALUES = ["0u", "1u", "42u", "255u", "4096u", "65535u"]
LONG_VALUES = ["0L", "1L", "-1L", "123456L", "-123456L", "2147483647L", "(-2147483647L-1)"]
ULONG_VALUES = ["0UL", "1UL", "123456UL", "305419896UL", "4294967295UL"]
LL_VALUES = ["0LL", "-1LL", "1234567890123LL", "-1234567890123LL"]
ULL_VALUES = ["0ULL", "1ULL", "1234567890123ULL", "18446744073709551615ULL"]


def spec_combos(conv, rng, count):
    """Random (flags, width, precision) combinations valid for *conv*."""
    flag_pool = ["", "-", "0", "+", " ", "-+", "0+", "+ ", "- "]
    if conv in "oxX":
        flag_pool = ["", "-", "0", "#", "-#", "0#"]
    elif conv == "u":
        flag_pool = ["", "-", "0"]
    widths = ["", "1", "3", "5", "8", "12"]
    precs = ["", ".0", ".1", ".3", ".5", ".8"]
    combos = list(itertools.product(flag_pool, widths, precs))
    rng.shuffle(combos)
    return combos[:count]


def int_cases(length, convs, values_for, seed, per_conv=70):
    rng = random.Random(seed)
    cases = []
    for conv in convs:
        values = values_for(conv)
        for flags, width, prec in spec_combos(conv, rng, per_conv):
            value = rng.choice(values)
            cases.append(("%%%s%s%s%s%s" % (flags, width, prec, length, conv), value))
    return cases


class TestIntegerFlags:
    def test_int(self, tmp_path):
        check(tmp_path, int_cases("", "di", lambda c: INT_VALUES, 1)
              + int_cases("", "uoxX", lambda c: UINT_VALUES, 2))

    def test_long(self, tmp_path):
        check(tmp_path, int_cases("l", "di", lambda c: LONG_VALUES, 3)
              + int_cases("l", "uoxX", lambda c: ULONG_VALUES, 4))

    def test_long_long(self, tmp_path):
        check(tmp_path, int_cases("ll", "di", lambda c: LL_VALUES, 5, 30)
              + int_cases("ll", "ux", lambda c: ULL_VALUES, 6, 30))

    def test_the_reported_cases(self, tmp_path):
        check(tmp_path, [
            ("%.4d", "42"), ("%.4u", "42u"), ("%.3d", "-7"), ("%5.3d", "7"),
            ("%-5d", "42"), ("%-5u", "42u"), ("%-5x", "255u"), ("%-8lu", "123456UL"),
            ("%8ld", "-123456L"), ("%08lx", "305419896UL"), ("%5s", '"ab"'),
            ("%-5s", '"ab"'), ("%.0d", "0"), ("%+d", "5"), ("% d", "5"),
            ("%#x", "255u"), ("%#o", "8u"), ("%#lx", "255UL"), ("%#X", "255u"),
            ("%#.0o", "0u"), ("%#.0lo", "0UL"), ("%#5.0o", "0u"), ("%#.0x", "0u"),
        ])


class TestHugeFields:
    def test_width_and_precision_over_255(self, tmp_path):
        """Digits used to wrap modulo 256 (%300d printed a 44 column field),
        then to stop at 255."""
        src = ('#include <stdio.h>\nint main(void){ int a = printf("%300d", 1);'
               ' int b = printf("|%.300d", 1); int c = printf("|%-1000s|", "a");'
               ' printf("\\n%d %d %d\\n", a, b, c); return 0; }\n')
        out = build_and_run(tmp_path, src).split("\n")
        assert out[1] == "300 301 1002", out[1]


class TestStringAndChar:
    def test_s_width_and_precision(self, tmp_path):
        strings = ['""', '"a"', '"hello"', '"hello world long"']
        cases = []
        for flags, width, prec in itertools.product(
                ["", "-"], ["", "1", "2", "5", "10", "20"],
                ["", ".0", ".2", ".5", ".10"]):
            for s in strings:
                cases.append(("%%%s%s%ss" % (flags, width, prec), s))
        check(tmp_path, cases)

    def test_c_width(self, tmp_path):
        check(tmp_path, [("%c", "'a'"), ("%3c", "'a'"), ("%-3c", "'a'"),
                         ("%1c", "'a'"), ("%-1c", "'a'"), ("%10c", "'z'")])

    def test_long_string_beyond_the_field(self, tmp_path):
        long_s = '"' + "0123456789" * 30 + '"'
        check(tmp_path, [("%s", long_s), ("%5s", long_s), ("%-5s", long_s)])


class TestStar:
    def test_star_width_and_precision(self, tmp_path):
        cases = [
            ("%*d", "5, 42"), ("%*d", "-5, 42"), ("%-*d", "5, 42"),
            ("%*d", "0, 42"), ("%.*d", "4, 42"), ("%.*d", "-1, 42"),
            ("%*.*d", "8, 4, 42"), ("%-*.*d", "8, 4, -42"),
            ("%0*d", "6, 42"), ("%*s", '8, "ab"'), ("%-*s", '8, "ab"'),
            ("%.*s", '2, "hello"'), ("%*.*s", '8, 2, "hello"'),
            ("%*x", "6, 255u"), ("%*ld", "10, 123456L"), ("%0*lx", "10, 255UL"),
            ("%*c", "4, 'x'"), ("%*d|%d", "4, 1, 2"), ("%d|%*d|%d", "1, 4, 2, 3"),
            ("%.*s|%d", '1, "xyz", 7'),
        ]
        check(tmp_path, cases)


class TestFeatureDetection:
    """printf links only the handlers a program names: keep that."""

    def test_d_only_program_has_no_long_or_float_handler(self, tmp_path):
        src = ('#include <stdio.h>\nint main(void){ printf("%5.3d|%-4d|\\n", 7, 8);'
               ' return 0; }\n')
        out = build_and_run(tmp_path, src)
        assert out == "  007|8   |\n"
        mac = (tmp_path / "prog.mac").read_text()
        assert "__printf_handle_ld" not in mac
        assert "__printf_handle_f" not in mac

    def test_sprintf_gets_the_same_formatting(self, tmp_path):
        src = ('#include <stdio.h>\nchar b[80];\n'
               'int main(void){ int n = sprintf(b, "%-6d|%.3x|%8ld|%5s|%*d", 42, 10, -5L, "ab", 4, 9);'
               ' printf("%s %d\\n", b, n); return 0; }\n')
        out = build_and_run(tmp_path, src)
        assert out == "42    |00a|      -5|   ab|   9 30\n"


class TestFieldCapNoWrap:
    """The padding count used to wrap when digits + precision zeros + sign
    + 0x prefix reached 256: a sign with precision 255 and a width made
    255 extra blanks.  Widths above 255 work (a host compiler gave the
    lengths below)."""

    def test_sign_and_prefix_with_large_precision(self, tmp_path):
        src = ('#include <stdio.h>\n'
               'int main(void){ int n;\n'
               ' n = printf("[%+300.255d]", 42); printf("|%d\\n", n);\n'
               ' n = printf("[%#400.254x]", 42); printf("|%d\\n", n);\n'
               ' n = printf("[%-+300.255d]", 42); printf("|%d\\n", n);\n'
               ' n = printf("[%+255.254d]", 42); printf("|%d\\n", n);\n'
               ' return 0; }\n')
        out = build_and_run(tmp_path, src)
        lines = out.splitlines()
        assert len(lines) == 4
        # sign + 255 digits = 256 characters, in a field of 300
        assert lines[0].endswith("|302") and len(lines[0]) == 302 + 4
        assert lines[1].endswith("|402") and len(lines[1]) == 402 + 4
        assert lines[2].endswith("|302")
        # sign + 254 digits = 255: exactly the width, no padding
        assert lines[3].endswith("|257") and len(lines[3]) == 257 + 4
