"""Tests for the printf format-dispatch table wiring (bug 4 part A).

The float conversions %e/%E/%g/%G/%F are registered in
``__printf_format_table`` alongside %f, but only the ones a unit really
uses: every table entry references its handler, so registering all six
unconditionally costs +1280 bytes on every program that prints a single %f.

Sibling files: tests/test_printf_warnings.py (compile-time diagnostic for a
conversion with no table entry), tests/test_printf_runtime_miss.py (the
run-time echo), tests/test_printf_features.py (--printf feature closure).
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
from uc_core.frontend import parse as _frontend_parse
from uc80.codegen import CodeGenerator

REPO = Path(__file__).resolve().parent.parent


def gen(source: str, **kwargs) -> str:
    """Generate assembly for *source* with the given CodeGenerator options."""
    unit = _frontend_parse(source, "<test>")
    return CodeGenerator("test", **kwargs).generate(unit)


def table_specs(code: str, table: str = "__printf_format_table") -> list:
    """Return the (spec, handler) pairs of one dispatch table, in order."""
    start = code.index(table + ":")
    body = code[start:]
    end = body.index("db\t0")
    return re.findall(r"db\t'(.)'\s*\n\s*dw\t(\S+)", body[:end])


def run_uc80(tmp_path, source: str, *args):
    """Drive the real CLI so preprocessor + codegen are both in the path."""
    src = tmp_path / "t.c"
    src.write_text(source)
    return subprocess.run(
        [sys.executable, "-m", "uc80.main", str(src), "-o", str(tmp_path / "t.mac"),
         *args],
        capture_output=True, text=True, cwd=str(REPO),
    )


class TestFloatConversionRegistration:
    """%e/%E/%g/%G/%F reach the dispatch table."""

    def test_e_is_registered(self):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%e", 1.0); return 0; }')
        assert ('e', '__printf_handle_e') in table_specs(code)

    def test_uppercase_e_uses_the_alias_entry_point(self):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%E", 1.0); return 0; }')
        assert ('E', '__printf_handle_eu') in table_specs(code)

    def test_g_is_registered(self):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%g", 1.0); return 0; }')
        assert ('g', '__printf_handle_g') in table_specs(code)

    def test_uppercase_g_uses_the_alias_entry_point(self):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%G", 1.0); return 0; }')
        assert ('G', '__printf_handle_gu') in table_specs(code)

    def test_uppercase_f_uses_the_alias_entry_point(self):
        """%F differs from %f: an infinity prints INF and a NaN NAN, so it
        goes through the alias that sets the uppercase flag, the same way
        %E and %G do."""
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%F", 1.0); return 0; }')
        assert ('F', '__printf_handle_fu') in table_specs(code)

    def test_handlers_get_an_extrn(self):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%e %g", 1.0, 2.0); return 0; }')
        assert "extrn\t__printf_handle_e" in code
        assert "extrn\t__printf_handle_g" in code

    def test_all_six_registered_for_explicit_features(self):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%f", 1.0); return 0; }',
                   printf_features={"int", "float"})
        specs = [s for s, _ in table_specs(code)]
        assert specs[-6:] == ['f', 'F', 'e', 'E', 'g', 'G']

    def test_all_six_registered_for_feature_all(self):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%f", 1.0); return 0; }',
                   printf_features={"all"})
        specs = [s for s, _ in table_specs(code)]
        for spec in 'fFeEgG':
            assert spec in specs


class TestPerSpecifierFilter:
    """The filter is mandatory: without it every float-using program grows
    by 1280 bytes because each table entry drags its handler module in."""

    def test_f_only_program_does_not_register_e_or_g(self):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%f", 1.0); return 0; }')
        specs = [s for s, _ in table_specs(code)]
        assert specs[-1] == 'f'
        for spec in 'FeEgG':
            assert spec not in specs
        assert "__printf_handle_e" not in code
        assert "__printf_handle_g" not in code

    def test_e_only_program_does_not_register_f(self):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%e", 1.0); return 0; }')
        specs = [s for s, _ in table_specs(code)]
        assert 'e' in specs and 'f' not in specs
        assert "__printf_handle_f" not in code

    def test_non_literal_format_string_registers_everything(self):
        # A non-literal format string forces features={"all"}: we cannot know
        # what it will ask for, so all six must be present.
        code = gen('int printf(const char*,...);'
                   'int main(const char *fmt){ printf(fmt, 1.0); return 0; }')
        specs = [s for s, _ in table_specs(code)]
        for spec in 'fFeEgG':
            assert spec in specs

    def test_float_table_entries_helper(self):
        entries = CodeGenerator._float_table_entries({'float', 'int', 'spec:g'})
        assert entries == [('g', '__printf_handle_g')]
        entries = CodeGenerator._float_table_entries({'float', 'int'})
        assert len(entries) == 6

    def test_hex_float_registers_nothing(self):
        # %a has no handler at all; it must not silently pull in %f's.
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%a", 1.0); return 0; }')
        specs = [s for s, _ in table_specs(code)]
        for spec in 'fFeEgGaA':
            assert spec not in specs


class TestLongTableFloatEntries:
    """%lf/%le/%lg go through __printf_long_table."""

    def test_lf_present_when_long_and_float_used(self):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%ld %f", 1L, 1.0); return 0; }')
        assert ('f', '__printf_handle_f') in table_specs(code, "__printf_long_table")

    def test_le_present_when_long_and_e_used(self):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%ld %e", 1L, 1.0); return 0; }')
        assert ('e', '__printf_handle_e') in table_specs(code, "__printf_long_table")

    def test_long_table_float_entries_are_filtered_too(self):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("%ld %f", 1L, 1.0); return 0; }')
        specs = [s for s, _ in table_specs(code, "__printf_long_table")]
        assert 'e' not in specs and 'g' not in specs


class TestTableSpecsMirror:
    """_printf_table_specs must describe what _emit_printf_format_tables
    actually emits -- the compile-time diagnostic depends on it."""

    @pytest.mark.parametrize("features", [
        {"int"}, {"float", "int"}, {"long", "int"}, {"llong", "long", "int"},
        {"all"}, {"float", "int", "spec:e"}, set(),
    ])
    def test_mirror_matches_emitted_tables(self, features):
        code = gen('int printf(const char*,...);'
                   'int main(void){ printf("x"); return 0; }',
                   printf_features=set(features))
        base, lng, ll = CodeGenerator._printf_table_specs(features)
        assert base == {s for s, _ in table_specs(code)}
        assert lng == {s for s, _ in table_specs(code, "__printf_long_table")}
        assert ll == {s for s, _ in table_specs(code, "__printf_ll_table")}


class TestReportedBugCLI:
    """The exact mbasic repro, through the real driver."""

    def test_e_and_g_reach_the_table(self, tmp_path):
        src = ('#include <stdio.h>\n'
               'int main(void){ float a=28.0;\n'
               '  printf("f=[%f]\\n",a); printf("e=[%e]\\n",a);'
               '  printf("g=[%g]\\n",a); return 0; }\n')
        r = run_uc80(tmp_path, src)
        assert r.returncode == 0, r.stderr
        code = (tmp_path / "t.mac").read_text()
        specs = [s for s, _ in table_specs(code)]
        assert 'f' in specs and 'e' in specs and 'g' in specs

    def test_explicit_printf_flags_also_work(self, tmp_path):
        src = ('#include <stdio.h>\n'
               'int main(void){ float a=28.0; printf("e=[%e]\\n",a); return 0; }\n')
        r = run_uc80(tmp_path, src, "--printf", "int", "--printf", "float")
        assert r.returncode == 0, r.stderr
        specs = [s for s, _ in table_specs((tmp_path / "t.mac").read_text())]
        assert 'e' in specs and 'E' in specs and 'g' in specs
