"""printf("text\\n") and printf("%s\\n", s) become puts() (docs/codegen_gap.md, B).

The rewrite runs in whole-program mode only, on a call that is a whole
expression statement (the result is unused).  A program with no other printf
call then does not link the printf core.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from uc_core.frontend import parse as _frontend_parse
from uc80.codegen import CodeGenerator

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"


def gen(source: str, **kwargs) -> str:
    unit = _frontend_parse("#include <stdio.h>\n" + source, "<test>")
    return CodeGenerator("test", **kwargs).generate(unit)


def calls(code: str) -> list:
    # A call loads the function address ("ld HL,_puts") then calls it.
    return re.findall(r"ld\s+HL,(_puts|_cputs|_printf)\b", code)


class TestRewriteDecision:
    def test_newline_literal_becomes_puts(self):
        code = gen('int main(void){ printf("hi\\n"); return 0; }',
                   whole_program=True)
        assert calls(code) == ["_puts"]

    def test_concatenated_literals_become_puts(self):
        code = gen('int main(void){ printf("a" "b\\n"); return 0; }',
                   whole_program=True)
        assert calls(code) == ["_puts"]

    def test_percent_s_newline_becomes_puts(self):
        code = gen('int main(void){ char *s = "x"; printf("%s\\n", s);'
                   ' return 0; }', whole_program=True)
        assert calls(code) == ["_puts"]

    def test_result_used_is_not_rewritten(self):
        code = gen('int main(void){ int r = printf("hi\\n"); return r; }',
                   whole_program=True)
        assert calls(code) == ["_printf"]

    def test_percent_in_literal_is_not_rewritten(self):
        code = gen('int main(void){ printf("100%%\\n"); printf("%d\\n", 1);'
                   ' return 0; }', whole_program=True)
        assert "_puts" not in calls(code)

    def test_kept_when_another_printf_call_remains(self):
        """puts would add library code to a program that links printf anyway."""
        code = gen('int main(void){ printf("hi\\n"); printf("%d\\n", 1);'
                   ' return 0; }', whole_program=True)
        assert calls(code) == ["_printf", "_printf"]

    def test_no_newline_becomes_cputs(self):
        code = gen('int main(void){ printf("hi"); return 0; }',
                   whole_program=True)
        assert calls(code) == ["_cputs"]

    def test_embedded_nul_is_not_rewritten(self):
        code = gen('int main(void){ printf("a\\0b\\n"); return 0; }',
                   whole_program=True)
        assert calls(code) == ["_printf"]

    def test_separate_compilation_is_not_rewritten(self):
        code = gen('int main(void){ printf("hi\\n"); return 0; }',
                   whole_program=False)
        assert calls(code) == ["_printf"]

    def test_user_defined_puts_is_not_rewritten(self):
        code = gen('int puts(const char *s){ return 7; }'
                   'int main(void){ printf("hi\\n"); return 0; }',
                   whole_program=True)
        assert calls(code) == ["_printf"]


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


needs_tools = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or built libraries not available")


def build_and_run(tmp_path, source, *flags):
    c_file = tmp_path / "prog.c"
    c_file.write_text("#include <stdio.h>\n" + source)
    mac, rel, com = (tmp_path / ("prog" + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac), *flags],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)], check=True,
                   capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=60, env=dict(os.environ))
    return run.stdout.decode("latin-1").replace("\r", ""), com.stat().st_size


@needs_tools
class TestRewriteRuns:
    def test_hello_does_not_link_printf_core(self, tmp_path):
        out, size = build_and_run(
            tmp_path, 'int main(void){ printf("hello\\n"); return 0; }')
        assert out == "hello\n"
        # With the printf core (and soft float) the image is about 3.9 KB.
        assert size < 600

    def test_escapes_and_concatenation_are_exact(self, tmp_path):
        src = (r'int main(void){ printf("a\tb \x41\101\\ \"q\" ?? " "c\n");'
               r' printf("\x68i\012"); printf("%s\n", "str");'
               r' printf("q\?\n"); return 0; }')
        out, _ = build_and_run(tmp_path, src)
        # the console makes spaces of the TAB: "a" and 7 spaces, then "b"
        assert out == 'a       b AA\\ "q" ?? c\nhi\nstr\nq?\n'

    def test_rewritten_output_equals_unrewritten(self, tmp_path):
        """The same program with a result-used printf (never rewritten)."""
        body = r'printf("x\ty\\z\n"); printf("%s\n", "w");'
        out1, _ = build_and_run(tmp_path, 'int main(void){ %s return 0; }' % body)
        out2, _ = build_and_run(
            tmp_path, 'int main(void){ int r; %s return 0; }'
            % body.replace("printf", "r = printf"))
        assert out1 == out2 == "x       y\\z\nw\n"   # TAB: 7 spaces


@needs_tools
class TestCputsAndLibrarySplit:
    def test_no_newline_text_runs_and_is_small(self, tmp_path):
        out, size = build_and_run(
            tmp_path, r'int main(void){ printf("a\x41" "b"); printf("c\n");'
                      r' return 0; }')
        assert out == "aAbc\n"
        out, size = build_and_run(
            tmp_path, 'int main(void){ printf("Hello"); return 0; }')
        assert out == "Hello"
        assert size < 600

    def test_empty_format_is_not_rewritten(self):
        code = gen('int main(void){ printf(""); return 0; }',
                   whole_program=True)
        assert calls(code) == ["_printf"]

    def test_integer_printf_does_not_link_float_printer(self, tmp_path):
        out, size = build_and_run(
            tmp_path, 'int main(void){ int i = 42; printf("%d x\\n", i);'
                      ' return 0; }')
        assert out == "42 x\n"
        assert size < 2000     # was 4352 with the float printer linked

    def test_float_printf_still_works(self, tmp_path):
        out, _ = build_and_run(
            tmp_path, 'int main(void){ double d = 3.25;'
                      ' printf("%f %g %e\\n", d, d, d); return 0; }')
        assert out == "3.250000 3.25 3.250000e+00\n"

    def test_console_fputs_does_not_link_disk_file_code(self, tmp_path):
        out, size = build_and_run(
            tmp_path, 'int main(void){ fputs("to stdout\\n", stdout);'
                      ' fputc(120, stderr); putc(10, stdout); return 0; }')
        assert out == "to stdout\nx\n"
        assert size < 1000

    def test_disk_file_write_and_read_back(self, tmp_path):
        out, _ = build_and_run(
            tmp_path, r'''int main(void){
 FILE *f = fopen("T.TMP", "w"); fputs("line one\n", f); fputc('Z', f);
 putc('\n', f); fwrite("abc\n", 1, 4, f); fprintf(f, "v=%d\n", 7); fclose(f);
 f = fopen("T.TMP", "r"); int c;
 while ((c = fgetc(f)) != EOF) putchar(c);
 fclose(f); remove("T.TMP"); return 0; }''')
        assert out == "line one\nZ\nabc\nv=7\n"

    def test_strdup_without_malloc_program_is_small(self, tmp_path):
        out, size = build_and_run(
            tmp_path, '#include <string.h>\nint main(void){ char b[8];'
                      ' strcpy(b, "ab"); puts(b); return strlen(b) - 2; }')
        assert out == "ab\n"
        assert size < 1300   # strdup (and malloc) no longer linked

    def test_strdup_still_works(self, tmp_path):
        out, _ = build_and_run(
            tmp_path, '#include <string.h>\n#include <stdlib.h>\n'
                      'int main(void){ char *p = strdup("dup"); puts(p);'
                      ' free(p); return 0; }')
        assert out == "dup\n"
