"""Front-end repairs of the bugfix4 round: a block typedef that redeclares a
file-scope typedef name, a typedef name after goto, an internal error with
file:line, #line and continued lines in the diagnostics of semcheck."""

import subprocess
import sys

from tests.test_struct_return_abi import REPO, build_and_run, pytestmark  # noqa: F401


def compile_c(tmp_path, text, name="t", *flags):
    (tmp_path / (name + ".c")).write_text(text)
    return subprocess.run(
        [sys.executable, "-m", "uc80.main", name + ".c", *flags,
         "-o", name + ".mac"],
        capture_output=True, text=True, cwd=str(tmp_path))


REDECL = r'''#include <stdio.h>
typedef int T;
int main(void) {
    T a = 5;
    {
        typedef long T;
        T v = 100000L;
        printf("%d %ld\n", (int) sizeof(T), v);
    }
    {
        T T = 3;
        { T = T + 1; }
        printf("%d\n", T);
    }
    {
        typedef char T;
        T c = 'x';
        { typedef long T; T w = 7L; printf("%d\n", (int) sizeof(T)); }
        printf("%d %c\n", (int) sizeof(T), c);
    }
    printf("%d\n", (int) sizeof(T) + a);
    return 0;
}
'''


def test_a_block_typedef_redeclares_a_file_scope_name(tmp_path):
    out = build_and_run(tmp_path, REDECL)
    assert out == "4 100000\n4\n4\n1 x\n7\n"


def test_a_block_typedef_with_int_32(tmp_path):
    out = build_and_run(tmp_path, REDECL, ("--int", "32"))
    assert out.splitlines()[0] == "4 100000"
    assert out.splitlines()[-1] == "9"       # sizeof(int) 4 + 5


def test_c_testsuite_00129_typedef_names_as_members_labels_and_goto(tmp_path):
    src = (REPO / "tests" / "c-testsuite-00129.c").read_text()
    r = compile_c(tmp_path, src)
    assert r.returncode == 0, r.stderr


def test_a_goto_label_is_not_a_typedef_name(tmp_path):
    out = build_and_run(tmp_path, r'''#include <stdio.h>
typedef int s;
int main(void) {
    int n = 0;
    goto s;
    n = 99;
    s: n += 1;
    printf("%d\n", n);
    return 0;
}
''')
    assert out == "1\n"


def test_an_internal_error_names_the_source_line(tmp_path):
    r = compile_c(tmp_path, "#define A 1\n#define B 2\n\nint x = A;\n"
                  "int main(void) { int y = = 3; return y; }\n")
    assert r.returncode == 1
    assert "internal error" in r.stderr and "t.c:5, column" in r.stderr


def test_an_internal_error_follows_a_line_directive(tmp_path):
    r = compile_c(tmp_path, '#line 50 "fake.c"\nint main(void) { int y = = 3; '
                  'return y; }\n')
    assert "at fake.c:50, column" in r.stderr


def test_semcheck_follows_a_line_directive(tmp_path):
    r = compile_c(tmp_path, '#line 100 "fake.c"\nint a;\nint f(void) '
                  '{ return undefined_x; }\n')
    assert r.returncode == 1
    assert "fake.c:101: 'undefined_x' undeclared" in r.stderr


def test_semcheck_after_a_continued_line(tmp_path):
    src = ('int f(void) { return 1 + \\\n   undefined_a; }\n'
           'const char *s = "abc\\\ndef"; int z = undefined_b;\n'
           'int g(void) { return undefined_c; }\n')
    r = compile_c(tmp_path, src)
    lines = r.stderr.splitlines()
    assert any(l.startswith("uc80: error: t.c:2: 'undefined_a'") for l in lines)
    assert any(l.startswith("uc80: error: t.c:4: 'undefined_b'") for l in lines)
    assert any(l.startswith("uc80: error: t.c:5: 'undefined_c'") for l in lines)


def test_semcheck_with_an_include_and_a_continued_string(tmp_path):
    (tmp_path / "h.h").write_text('const char *hs = "one\\\ntwo";\n'
                                  'int hf(void) { return bad_h; }\n')
    r = compile_c(tmp_path, '#include "h.h"\nint main(void) { return 0; }\n')
    assert "h.h:3: 'bad_h' undeclared" in r.stderr
