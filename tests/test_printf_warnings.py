"""Compile-time diagnostics for printf conversions with no handler (D7).

A dispatch-table miss is not merely an empty field: only a handler advances
the vararg offset, so a miss leaves every later conversion in the same call
reading the wrong argument.  Wherever the format string is a literal the
compiler can say so before the program is ever run.  (The complementary
run-time echo lives in tests/test_printf_runtime_miss.py; it covers the
non-literal format strings the compiler cannot see.)
"""

from uc_core.frontend import parse as _frontend_parse
from uc80.codegen import CodeGenerator


def warnings_for(source: str, **kwargs) -> list:
    gen = CodeGenerator("test", **kwargs)
    gen.generate(_frontend_parse(source, "<test>"))
    return gen.warnings


DECL = 'int printf(const char*,...);'


class TestCompileTimeWarning:
    """Literal format strings are diagnosed before the program is ever run."""

    def test_unsupported_conversion_warns(self):
        w = warnings_for(DECL + 'int main(void){ printf("%q", 1); return 0; }')
        assert len(w) == 1
        assert "'%q'" in w[0] and "no handler" in w[0]

    def test_warning_names_the_consequence(self):
        w = warnings_for(DECL + 'int main(void){ printf("%q", 1); return 0; }')
        assert "wrong argument" in w[0]

    def test_hex_float_warns(self):
        """%a routes to the float bucket but has no handler."""
        w = warnings_for(DECL + 'int main(void){ printf("%a", 1.0); return 0; }')
        assert any("'%a'" in m for m in w)

    def test_percent_n_warns(self):
        w = warnings_for(DECL + 'int main(void){ int n; printf("%n", &n); return 0; }')
        assert any("'%n'" in m for m in w)

    def test_length_modifier_is_kept_in_the_message(self):
        w = warnings_for(DECL + 'int main(void){ printf("%ls", "x"); return 0; }')
        assert any("'%ls'" in m for m in w)

    def test_supported_conversions_are_silent(self):
        w = warnings_for(DECL + 'int main(void){ printf("%d %s %c %x %f %e %g %ld",'
                                ' 1, "s", 65, 2, 1.0, 1.0, 1.0, 1L); return 0; }')
        assert w == []

    def test_each_conversion_warns_only_once(self):
        w = warnings_for(DECL + 'int main(void){ printf("%q%q%q", 1, 2, 3); return 0; }')
        assert len(w) == 1

    def test_no_printf_no_warnings(self):
        assert warnings_for('int main(void){ return 0; }') == []


class TestFeatureGapWarning:
    """The --printf trap: a conversion the selected feature set drops."""

    def test_float_conversion_without_float_feature(self):
        w = warnings_for(DECL + 'int main(void){ printf("%f", 1.0); return 0; }',
                         printf_features={"int"})
        assert len(w) == 1
        assert "'%f'" in w[0] and "selected printf features (int)" in w[0]

    def test_int_conversion_without_int_feature(self):
        w = warnings_for(DECL + 'int main(void){ printf("%d", 1); return 0; }',
                         printf_features={"float"})
        assert any("'%d'" in m for m in w)

    def test_feature_all_covers_everything_real(self):
        w = warnings_for(DECL + 'int main(void){ printf("%d %f %e %ld %llx",'
                                ' 1, 1.0, 1.0, 1L, 1LL); return 0; }',
                         printf_features={"all"})
        assert w == []

    def test_message_distinguishes_gap_from_unsupported(self):
        w = warnings_for(DECL + 'int main(void){ printf("%f %q", 1.0, 1); return 0; }',
                         printf_features={"int"})
        gap = [m for m in w if "'%f'" in m]
        bad = [m for m in w if "'%q'" in m]
        assert gap and "selected printf features" in gap[0]
        assert bad and "no handler in this libc" in bad[0]
