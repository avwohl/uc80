"""printf link size (family K steps 1, 2 and 4).

- printf("\\n") and printf("x") become putchar, so the printf core is not linked.
- A program without a printf call has no printf dispatch tables.
- A format that is not a literal links the conversions of all string
  literals, not every conversion.
Each test runs the program with uc80 and with the host compiler and compares
the output.
"""

import re
import shutil
import subprocess
import sys

import pytest

from .test_k1_divide import run_both
from .test_struct_return_abi import LIB_DIR, REPO, _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available() or not shutil.which("cc"),
    reason="um80/ul80/cpmemu, the built libraries or cc are not available")

NEWLINE_ONLY = r"""
#include <stdio.h>
int main(void)
{
    putchar('A');
    printf("\n");
    printf("x");
    printf("\n");
    printf("%%");
    return 0;
}
"""

ONLY_ONE_CHAR = r"""
#include <stdio.h>
int main(void)
{
    int i;
    for (i = 0; i < 3; i++) {
        printf("a");
        printf("\n");
    }
    return 0;
}
"""


def has_call(text, name):
    return re.search(r"\tcall\t" + name + r"\b", text) is not None


def test_printf_of_one_char_becomes_putchar(tmp_path):
    want, got, text = run_both(tmp_path, ONLY_ONE_CHAR)
    assert got == want
    assert not has_call(text, "_printf")
    assert has_call(text, "__putchar_v")
    assert "__printf_format_table" not in text      # no tables either


def test_percent_format_keeps_printf(tmp_path):
    """The format "%%" is not one character of output text for the rule."""
    want, got, text = run_both(tmp_path, NEWLINE_ONLY)
    assert got == want
    assert has_call(text, "_printf")


def test_options_keep_printf(tmp_path):
    want, got, text = run_both(tmp_path, ONLY_ONE_CHAR,
                               "--no-printf-to-putchar")
    assert got == want
    assert has_call(text, "_printf")
    want, got, text = run_both(tmp_path, ONLY_ONE_CHAR,
                               "--no-void-putchar")
    assert got == want
    assert has_call(text, "_printf")
    want, got, text = run_both(tmp_path, ONLY_ONE_CHAR,
                               "--no-drop-printf-tables")
    assert got == want
    assert not has_call(text, "_printf")
    assert "__printf_format_table:" in text


MIXED = r"""
#include <stdio.h>
int main(void)
{
    int n = 7;
    printf("\n");
    printf("%d\n", n);
    printf("z");
    n = printf("\n");
    printf("%d\n", n);
    return 0;
}
"""


def test_mixed_calls_keep_printf(tmp_path):
    want, got, text = run_both(tmp_path, MIXED)
    assert got == want
    assert has_call(text, "_printf")


def test_result_used_keeps_printf(tmp_path):
    src = r"""
#include <stdio.h>
int main(void)
{
    int r = printf("\n");
    printf("%d\n", r);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want


OWN_PUTCHAR = r"""
#include <stdio.h>
int putchar(int c)
{
    return fputc(c + 1, stdout);
}
int main(void)
{
    printf("a");
    return 0;
}
"""


def test_own_putchar_is_not_called(tmp_path):
    """printf writes the char itself, not through a putchar of the program."""
    src = OWN_PUTCHAR
    mac = tmp_path / "o.mac"
    (tmp_path / "o.c").write_text(src)
    r = subprocess.run([sys.executable, "-m", "uc80.main",
                        str(tmp_path / "o.c"), "-o", str(mac)],
                       capture_output=True, text=True, cwd=str(REPO),
                       timeout=300)
    assert r.returncode == 0, r.stderr
    text = mac.read_text()
    assert not has_call(text, "_putchar") and not has_call(text, "__putchar_v")


def test_no_tables_without_printf(tmp_path):
    src = "int main(void) { return 0; }\n"
    (tmp_path / "n.c").write_text(src)
    for flags, tables in (((), False), (("--no-drop-printf-tables",), True)):
        mac = tmp_path / "n.mac"
        r = subprocess.run([sys.executable, "-m", "uc80.main",
                            str(tmp_path / "n.c"), "-o", str(mac), *flags],
                           capture_output=True, text=True, cwd=str(REPO),
                           timeout=300)
        assert r.returncode == 0, r.stderr
        assert ("__printf_format_table:" in mac.read_text()) == tables


WRAPPER = r"""
#include <stdio.h>
#include <stdarg.h>
static void px(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stdout, fmt, ap);
    va_end(ap);
}
int main(void)
{
    px("%d %s %u|%6s|%02d|%-5s|\n", -5, "hi", 7u, "ab", 3, "cd");
    px("%x %c\n", 255u, 'q');
@MORE@
    return 0;
}
"""


def link_size(tmp_path, source, *flags):
    (tmp_path / "w.c").write_text(source)
    mac = tmp_path / "w.mac"
    r = subprocess.run([sys.executable, "-m", "uc80.main",
                        str(tmp_path / "w.c"), "-o", str(mac), *flags],
                       capture_output=True, text=True, cwd=str(REPO),
                       timeout=300)
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(tmp_path / "w.rel")],
                   check=True, capture_output=True, timeout=120)
    subprocess.run(["ul80", str(tmp_path / "w.rel"), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o",
                    str(tmp_path / "w.com")], check=True, capture_output=True,
                   timeout=120)
    return (tmp_path / "w.com").stat().st_size


def test_wrapper_links_only_the_literal_conversions(tmp_path):
    src = WRAPPER.replace("@MORE@", "")
    want, got, text = run_both(tmp_path, src)
    assert got == want
    small = link_size(tmp_path, src)
    big = link_size(tmp_path, src, "--no-printf-literal-union")
    assert small + 1000 < big, (small, big)


def test_wrapper_with_a_float_literal_prints_floats(tmp_path):
    src = WRAPPER.replace("@MORE@", '    px("%.2f %5.1f\\n", 1.5, -2.25);\n')
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "1.50" in got


def test_wrapper_with_every_conversion_option(tmp_path):
    src = WRAPPER.replace("@MORE@", '    px("%ld %lu %lx\\n", 100000L, 70000UL, 65536UL);\n')
    want, got, text = run_both(tmp_path, src)
    assert got == want
    want, got, text = run_both(tmp_path, src, "--no-printf-literal-union")
    assert got == want


def test_input_format_keeps_every_conversion(tmp_path):
    """No string literal has a conversion: the format is input, so the
    float printer stays linked."""
    src = r"""
#include <stdio.h>
int main(int argc, char **argv)
{
    char fmt[8];
    fmt[0] = '%'; fmt[1] = '.'; fmt[2] = '1'; fmt[3] = 'f'; fmt[4] = '\n';
    fmt[5] = 0;
    printf(fmt, 2.5);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "2.5"
