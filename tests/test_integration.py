"""Integration tests for multi-file compilation and optimizations."""

import subprocess
import sys
from pathlib import Path


def run_compiler(*args):
    """Run the uc80 compiler with given arguments."""
    result = subprocess.run(
        [sys.executable, "-m", "uc80.main", *args],
        capture_output=True,
        text=True
    )
    return result


class TestMultiFileCompilation:
    """Integration tests for multi-file compilation."""

    def test_compile_multiple_files(self, tmp_path):
        """Compile multiple C files together."""
        # Create test files - use non-trivial function to avoid inlining
        util_c = tmp_path / "util.c"
        util_c.write_text("""
            int compute(int a, int b) {
                int x = a + b;
                int y = x * 2;
                return y - a;
            }
        """)

        main_c = tmp_path / "main.c"
        main_c.write_text("""
            int compute(int a, int b);
            int main(void) { return compute(1, 2); }
        """)

        output = tmp_path / "test.mac"

        result = run_compiler(str(main_c), str(util_c), "-o", str(output))
        assert result.returncode == 0, f"Compiler failed: {result.stderr}"
        assert output.exists()

        code = output.read_text().lower()
        assert "public\t_main" in code
        assert "public\t_compute" in code

    def test_dead_function_elimination(self, tmp_path):
        """Dead functions are eliminated in multi-file compilation."""
        util_c = tmp_path / "util.c"
        util_c.write_text("""
            void used(void) { }
            void unused(void) { }
        """)

        main_c = tmp_path / "main.c"
        main_c.write_text("""
            void used(void);
            int main(void) { used(); return 0; }
        """)

        output = tmp_path / "test.mac"

        result = run_compiler(str(main_c), str(util_c), "-o", str(output))
        assert result.returncode == 0

        code = output.read_text().lower()
        assert "public\t_used" in code
        assert "public\t_unused" not in code

    def test_cross_file_inlining(self, tmp_path):
        """Functions from one file can be inlined into another."""
        util_c = tmp_path / "util.c"
        util_c.write_text("""
            int inc(int x) { return x + 1; }
        """)

        main_c = tmp_path / "main.c"
        main_c.write_text("""
            int inc(int x);
            int main(void) { return inc(5); }
        """)

        output = tmp_path / "test.mac"

        result = run_compiler(str(main_c), str(util_c), "-o", str(output), "-v")
        assert result.returncode == 0

        # inc should be inlined and eliminated
        code = output.read_text().lower()
        assert "public\t_inc" not in code
        assert "Inlined" in result.stdout

    def test_shared_storage_across_files(self, tmp_path):
        """Shared storage works across files."""
        util_c = tmp_path / "util.c"
        util_c.write_text("""
            void helper(void) { int x = 1; int y = 2; }
        """)

        main_c = tmp_path / "main.c"
        main_c.write_text("""
            void helper(void);
            int main(void) { int a = 1; helper(); return a; }
        """)

        output = tmp_path / "test.mac"

        result = run_compiler(str(main_c), str(util_c), "-o", str(output))
        assert result.returncode == 0

        code = output.read_text()
        assert "??AUTO" in code
        assert "uses shared storage" in code

    def test_no_whole_program_preserves_functions(self, tmp_path):
        """With --no-whole-program, PUBLIC functions are preserved."""
        main_c = tmp_path / "main.c"
        main_c.write_text("""
            void api_func(void) { }
            int main(void) { return 0; }
        """)

        output = tmp_path / "test.mac"

        result = run_compiler(str(main_c), "-o", str(output), "--no-whole-program")
        assert result.returncode == 0

        code = output.read_text().lower()
        # api_func should be preserved even though not called
        assert "public\t_api_func" in code

    def test_verbose_output(self, tmp_path):
        """Verbose output shows optimization statistics."""
        main_c = tmp_path / "main.c"
        main_c.write_text("""
            int inc(int x) { return x + 1; }
            void unused(void) { }
            int main(void) { return inc(5); }
        """)

        output = tmp_path / "test.mac"

        result = run_compiler(str(main_c), "-o", str(output), "-v")
        assert result.returncode == 0

        # Check verbose output mentions optimizations
        assert "Inlined" in result.stdout
        assert "Eliminated" in result.stdout


class TestOptimizationFlags:
    """Test optimization control flags."""

    def test_no_shared_storage(self, tmp_path):
        """--no-shared-storage disables shared storage."""
        main_c = tmp_path / "main.c"
        main_c.write_text("""
            void foo(void) { int x = 1; }
            int main(void) { foo(); return 0; }
        """)

        output = tmp_path / "test.mac"

        result = run_compiler(str(main_c), "-o", str(output),
                            "--no-shared-storage", "--no-inlining",
                            "--no-dead-elimination", "--no-const-propagation")
        assert result.returncode == 0

        code = output.read_text()
        assert "??AUTO" not in code

    def test_no_inlining(self, tmp_path):
        """--no-inlining prevents function inlining."""
        main_c = tmp_path / "main.c"
        main_c.write_text("""
            int inc(int x) { return x + 1; }
            int main(void) { return inc(5); }
        """)

        output = tmp_path / "test.mac"

        result = run_compiler(str(main_c), "-o", str(output), "--no-inlining")
        assert result.returncode == 0

        code = output.read_text().lower()
        # inc should NOT be inlined
        assert "public\t_inc" in code
        assert "call\t_inc" in code

    def test_no_dead_elimination(self, tmp_path):
        """--no-dead-elimination keeps unused functions."""
        main_c = tmp_path / "main.c"
        main_c.write_text("""
            void unused(void) { }
            int main(void) { return 0; }
        """)

        output = tmp_path / "test.mac"

        result = run_compiler(str(main_c), "-o", str(output), "--no-dead-elimination")
        assert result.returncode == 0

        code = output.read_text().lower()
        assert "public\t_unused" in code


class TestUserFacingDiagnostics:
    """A CodegenError must reach the user as a clean diagnostic."""

    def test_conflicting_array_sizes_are_diagnosed(self, tmp_path):
        """Conflicting array sizes exit non-zero with 'uc80: error:'."""
        src = tmp_path / "arr.c"
        src.write_text("extern int arr[100];\nint arr[200];\n")

        result = run_compiler(str(src), "-o", str(tmp_path / "arr.mac"))

        assert result.returncode != 0
        assert "uc80: error:" in result.stderr
        assert "conflicting types for 'arr'" in result.stderr
        # A source fault is not a compiler bug: no "internal error", and
        # no Python traceback leaking through.
        assert "internal error" not in result.stderr
        assert "Traceback" not in result.stderr

    def test_matching_array_sizes_compile(self, tmp_path):
        """The same extern and definition size is accepted (was a crash)."""
        src = tmp_path / "arr.c"
        src.write_text("extern int arr[100];\nint arr[100];\n")
        output = tmp_path / "arr.mac"

        result = run_compiler(str(src), "-o", str(output))

        assert result.returncode == 0, f"Compiler failed: {result.stderr}"
        assert "ds\t200" in output.read_text()
