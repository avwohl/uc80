"""Tests for --printf/--scanf feature-set closure (D9).

``--printf float`` alone used to make %d print nothing AND corrupt every
later field in the same call, because the dispatch table is one flat list
and only a handler advances the vararg offset.  float/long/llong now imply
int, and llong implies long -- which is exactly what the compiler's own
auto-detector has always assumed (_extract_printf_specifiers adds 'int'
alongside 'float'/'long').
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def table_specs(code: str, table: str = "__printf_format_table") -> list:
    """Return the (spec, handler) pairs of one dispatch table, in order."""
    start = code.index(table + ":")
    body = code[start:]
    end = body.index("db\t0")
    return re.findall(r"db\t'(.)'\s*\n\s*dw\t(\S+)", body[:end])


def run_uc80(tmp_path, source: str, *args):
    """Drive the real CLI so the argparse layer is in the path."""
    src = tmp_path / "t.c"
    src.write_text(source)
    return subprocess.run(
        [sys.executable, "-m", "uc80.main", str(src), "-o", str(tmp_path / "t.mac"),
         *args],
        capture_output=True, text=True, cwd=str(REPO),
    )


class TestPrintfFeatureClosure:
    """D9: --printf float|long|llong implies int."""

    def test_float_implies_int(self):
        from uc80.main import _close_format_features
        assert _close_format_features({"float"}) == {"float", "int"}

    def test_long_implies_int(self):
        from uc80.main import _close_format_features
        assert _close_format_features({"long"}) == {"long", "int"}

    def test_llong_implies_long_and_int(self):
        from uc80.main import _close_format_features
        assert _close_format_features({"llong"}) == {"llong", "long", "int"}

    def test_int_alone_is_unchanged(self):
        from uc80.main import _close_format_features
        assert _close_format_features({"int"}) == {"int"}

    def test_all_is_left_alone(self):
        from uc80.main import _close_format_features
        assert _close_format_features({"all"}) == {"all"}

    def test_input_set_is_not_mutated(self):
        from uc80.main import _close_format_features
        given = {"float"}
        _close_format_features(given)
        assert given == {"float"}

    def test_printf_float_still_gets_the_int_conversions(self, tmp_path):
        """--printf float alone used to make %d print nothing AND corrupt
        every later field in the same call."""
        r = run_uc80(tmp_path, '#include <stdio.h>\n'
                               'int main(void){ printf("%d %s %f", 1, "s", 1.0);'
                               ' return 0; }',
                     "--printf", "float")
        assert r.returncode == 0, r.stderr
        specs = [s for s, _ in table_specs((tmp_path / "t.mac").read_text())]
        for spec in 'dsuxc':
            assert spec in specs
        assert 'f' in specs
        assert "warning" not in r.stderr

    def test_printf_llong_still_gets_the_long_table(self, tmp_path):
        """--printf llong used to yield %lld without %ld."""
        r = run_uc80(tmp_path, '#include <stdio.h>\n'
                               'int main(void){ printf("%ld %lld %d", 1L, 1LL, 1);'
                               ' return 0; }',
                     "--printf", "llong")
        assert r.returncode == 0, r.stderr
        code = (tmp_path / "t.mac").read_text()
        assert ('d', '__printf_handle_ld') in table_specs(code, "__printf_long_table")
        assert ('d', '__printf_handle_lld') in table_specs(code, "__printf_ll_table")
        assert "warning" not in r.stderr

    def test_pragma_printf_is_closed_too(self, tmp_path):
        r = run_uc80(tmp_path, '#pragma printf float\n'
                               'int printf(const char*,...);\n'
                               'int main(void){ printf("%d %f", 1, 1.0); return 0; }')
        assert r.returncode == 0, r.stderr
        specs = [s for s, _ in table_specs((tmp_path / "t.mac").read_text())]
        assert 'd' in specs and 'f' in specs


class TestWarningsReachStderr:
    """main.py is what actually surfaces the codegen diagnostics."""

    def test_unknown_conversion_warns_on_stderr(self, tmp_path):
        r = run_uc80(tmp_path, 'int printf(const char*,...);\n'
                               'int main(void){ printf("%q", 1); return 0; }')
        assert r.returncode == 0, r.stderr
        assert "uc80: warning:" in r.stderr and "'%q'" in r.stderr

    def test_a_warning_is_not_an_error(self, tmp_path):
        r = run_uc80(tmp_path, 'int printf(const char*,...);\n'
                               'int main(void){ printf("%q", 1); return 0; }')
        assert r.returncode == 0
        assert "error" not in r.stderr and "Traceback" not in r.stderr

    def test_clean_source_is_silent(self, tmp_path):
        r = run_uc80(tmp_path, 'int printf(const char*,...);\n'
                               'int main(void){ printf("%d %e", 1, 1.0); return 0; }')
        assert r.returncode == 0, r.stderr
        assert "warning" not in r.stderr
