"""End-to-end regression tests for bundled headers under --int=16 and --int=32.

Phase 4 rewrote limits.h, stdint.h, stddef.h, and inttypes.h to derive from
compiler-supplied __SIZEOF_*__ / __*_MAX__ macros.  These tests compile a
fixture that uses each header's typedefs and macros and verify the compile
completes cleanly under both integer-size configurations — guarding against
regressions that would force source edits for one config or the other.
"""

import subprocess
import sys
from pathlib import Path

import pytest


def _compile(src: str, tmp_path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    c_file = tmp_path / "hdr_test.c"
    c_file.write_text(src)
    out = tmp_path / "hdr_test.mac"
    return subprocess.run(
        [sys.executable, "-m", "src.main", str(c_file), "-o", str(out), *extra_args],
        capture_output=True, text=True,
    )


# Each parametrize row: (id, cli args, expected sizeof(int))
_CONFIGS = [
    ("default_int16", (), 2),
    ("int32", ("--int=32",), 4),
]


@pytest.mark.parametrize("label, args, sizeof_int", _CONFIGS, ids=[c[0] for c in _CONFIGS])
class TestBundledHeaders:

    def test_limits_h_compiles(self, tmp_path, label, args, sizeof_int):
        """limits.h: INT_MIN/MAX, LONG_MIN/MAX, CHAR_BIT must all be usable."""
        src = """
        #include <limits.h>
        int a = INT_MAX;
        int b = INT_MIN;
        long c = LONG_MAX;
        long d = LONG_MIN;
        int e = CHAR_BIT;
        int f = SCHAR_MAX;
        int main(void) { return a + e + f; }
        """
        r = _compile(src, tmp_path, *args)
        assert r.returncode == 0, f"limits.h failed under {label}: {r.stderr[:300]}"

    def test_stdint_h_typedefs_and_limits(self, tmp_path, label, args, sizeof_int):
        """stdint.h: exact-width typedefs and INT{16,32,64}_MAX macros."""
        src = """
        #include <stdint.h>
        int8_t  a8  = INT8_MAX;
        int16_t a16 = INT16_MAX;
        int32_t a32 = INT32_MAX;
        uint16_t u16 = UINT16_MAX;
        uint32_t u32 = UINT32_MAX;
        intptr_t p = INTPTR_MAX;
        intmax_t m = INTMAX_MAX;
        int main(void) { return a8 + a16; }
        """
        r = _compile(src, tmp_path, *args)
        assert r.returncode == 0, f"stdint.h failed under {label}: {r.stderr[:300]}"

    def test_stddef_h_size_t(self, tmp_path, label, args, sizeof_int):
        """stddef.h: size_t / ptrdiff_t track __SIZEOF_POINTER__, not int."""
        src = """
        #include <stddef.h>
        size_t s = sizeof(int);
        ptrdiff_t d;
        int main(void) { return s; }
        """
        r = _compile(src, tmp_path, *args)
        assert r.returncode == 0, f"stddef.h failed under {label}: {r.stderr[:300]}"

    def test_inttypes_h_format_macros(self, tmp_path, label, args, sizeof_int):
        """inttypes.h: PRI*/SCN* length modifiers.  With --int=32 PRId32 is ""
        (plain %d); with default --int=16 it's "l" (→ %ld).  Either way the
        header must paste cleanly into a printf call."""
        src = """
        #include <stdio.h>
        #include <inttypes.h>
        #include <stdint.h>
        int32_t x = 42;
        int16_t y = 7;
        int main(void) {
            printf("%" PRId32 " %" PRId16 "\\n", x, y);
            return 0;
        }
        """
        r = _compile(src, tmp_path, *args)
        assert r.returncode == 0, f"inttypes.h failed under {label}: {r.stderr[:300]}"

    def test_sizeof_int_matches_config(self, tmp_path, label, args, sizeof_int):
        """sizeof(int) at compile time must match the CLI int-size.  The
        AST optimizer folds this to a literal, so we can verify via the
        generated .mac output."""
        c = tmp_path / "so.c"
        c.write_text("int main(void) { return sizeof(int); }\n")
        out = tmp_path / "so.mac"
        r = subprocess.run(
            [sys.executable, "-m", "src.main", str(c), "-o", str(out), *args],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
        asm = out.read_text()
        # The literal sizeof value should appear as "ld HL,<n>" somewhere
        assert f"HL,{sizeof_int}" in asm or f"hl,{sizeof_int}" in asm, \
            f"expected sizeof(int)={sizeof_int} under {label}; generated .mac doesn't load it"


class TestMacrosAgreeWithRuntimeSizeof:
    """Spot-check that preprocessor-visible __SIZEOF_*__ matches sizeof()."""

    def test_int_size_macro_matches_sizeof(self, tmp_path):
        """__SIZEOF_INT__ (preprocessor) == sizeof(int) (compile-time fold)."""
        c = tmp_path / "m.c"
        c.write_text("""
        #if __SIZEOF_INT__ == 4
        int marker_int32 = 1;
        #define MARKER marker_int32
        #else
        int marker_int16 = 1;
        #define MARKER marker_int16
        #endif
        int main(void) { return MARKER + (sizeof(int) == __SIZEOF_INT__); }
        """)
        out = tmp_path / "m.mac"
        # default
        r = subprocess.run([sys.executable, "-m", "src.main", str(c), "-o", str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 0
        assert "marker_int16" in out.read_text()
        # int=32
        r = subprocess.run([sys.executable, "-m", "src.main", "--int=32", str(c), "-o", str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 0
        assert "marker_int32" in out.read_text()
