"""Repairs of the fourth round: fixed-type enumerations in inlined calls,
typeof and _Generic of their constants, their diagnostics; block typedefs;
the line of a diagnostic after comment splices, #line in an include file;
constants in enumerators; a string element in a static initializer."""

import subprocess
import sys

import pytest

from tests.test_struct_return_abi import (  # noqa: F401
    REPO, build_and_run, pytestmark)

FLAGS = [(), ("--int=32",)]
IDS = ["int16", "int32"]


def compile_c(tmp_path, text, name="e.c", *flags):
    (tmp_path / name).write_text(text)
    return subprocess.run([sys.executable, "-m", "uc80.main", name, "-o",
                           "e.mac", *flags], capture_output=True, text=True,
                          cwd=str(tmp_path))


@pytest.mark.parametrize("flags", FLAGS, ids=IDS)
def test_an_inlined_call_converts_to_a_fixed_enum_type(tmp_path, flags):
    out = build_and_run(tmp_path, r'''#include <stdio.h>
enum U : unsigned char { U7 = 7 };
enum S : signed char { S1 = 1 };
enum W : unsigned short { W1 = 1 };
enum U r1(int x) { return x; }
enum S r2(int x) { return x; }
enum W r3(long x) { return x; }
unsigned char r4(int x) { return x; }
int a1(enum U u) { return u; }
int a2(enum S s) { return s; }
int main(void) {
    printf("%d %d %d %d\n", r1(300), r2(200), (int) r3(70000L), r4(300));
    printf("%d %d\n", a1(300), a2(200));
    printf("%d\n", r1(300) == 44);
    printf("%d %d\n", (int) r3(70000), r4(300));
    return 0;
}
''', flags)
    assert out == "44 -56 4464 44\n44 -56\n1\n4464 44\n"


@pytest.mark.parametrize("flags", FLAGS, ids=IDS)
def test_typeof_and_generic_of_fixed_enum_constants(tmp_path, flags):
    out = build_and_run(tmp_path, r'''#include <stdio.h>
enum U : unsigned char { U1 = 1, U2 = 255 };
enum c : char { A = 1 };
enum S : signed char { S1 = -1 };
int main(void) {
    printf("%d\n", (int) sizeof(__typeof__(U1)));
    __typeof__(U2) x = 300;
    printf("%d\n", x);
    printf("%d %d\n", _Generic(A, char: 1, signed char: 2, unsigned char: 3, default: 4),
           _Generic(S1, char: 1, signed char: 2, unsigned char: 3, default: 4));
    enum c cc = A;
    printf("%d %d\n", _Generic(cc, char: 1, signed char: 2, default: 4), (int) sizeof(cc));
    return 0;
}
''', flags)
    assert out == "1\n44\n1 2\n1 1\n"


@pytest.mark.parametrize("text,message", [
    ("enum e : int * { A = 0 };", "e.c:1: enum e: the underlying type "
     "'int *' is not an integer type"),
    ("enum e : signed char {\n A = 1,\n B = 200 };", "e.c:1: enumerator 'B' "
     "of 'enum e' has the value 200"),
    ("#line 40 \"fake.c\"\nenum e : unsigned char { A = 256 };",
     "fake.c:40: enumerator 'A'"),
    ("enum e : signed char { A = 128 };", "underlying type signed char "
     "(-128..127"),
    ("enum e : short; enum e : long { A };", "e.c:1: enum e is declared "
     "with the underlying types"),
    ("enum e : char { A, B, A };", "e.c:1: redeclaration of enumerator 'A'"),
])
def test_fixed_enum_diagnostics_have_file_and_line(tmp_path, text, message):
    r = compile_c(tmp_path, text + "\nint main(void) { return 0; }\n")
    assert r.returncode == 1
    assert message in r.stderr


@pytest.mark.parametrize("flags", FLAGS, ids=IDS)
def test_typedef_scopes(tmp_path, flags):
    out = build_and_run(tmp_path, r'''#include <stdio.h>
typedef int T;
struct T { int a; int b; };
int main(void) {
    { typedef long long T; struct T s; s.a = 1; s.b = 2;
      printf("%d %d\n", (int) sizeof(T), (int) sizeof(s) == (int) sizeof(struct T)); }
    printf("%d\n", (int) sizeof(T) == (int) sizeof(int));
    typedef long long L;
    { typedef char L; printf("%d\n", (int) sizeof(L)); }
    printf("%d\n", (int) sizeof(L));
    { typedef short L; { typedef char L; printf("%d\n", (int) sizeof(L)); }
      printf("%d\n", (int) sizeof(L)); }
    printf("%d\n", (int) sizeof(L));
    { T v = 100000L; printf("%d\n", v == 100000L); }
    return 0;
}
''', flags)
    assert out == "8 1\n1\n1\n8\n1\n2\n8\n1\n"


def test_enumerators_with_constant_expressions(tmp_path):
    out = build_and_run(tmp_path, r'''#include <stdio.h>
#include <stddef.h>
typedef long long LL;
struct S { char a; int b; char c[5]; };
enum { A = sizeof(int), B = sizeof(char[9]), C = 2 + 3, D = sizeof(long) + 1,
       E = sizeof(LL), F = offsetof(struct S, b), G = (3 > 2) ? 7 : 9 };
int main(void) {
    printf("%d %d %d %d %d %d %d\n", A == (int) sizeof(int), B, C,
           D == (int) sizeof(long) + 1, E, F, G);
    { typedef long long T; enum { X = sizeof(T), Y }; printf("%d %d\n", X, Y); }
    return 0;
}
''')
    assert out == "1 9 5 1 8 1 7\n8 9\n"


def test_a_string_element_is_no_static_initializer(tmp_path):
    r = compile_c(tmp_path, 'int d = 5 + "xy"[0];\nint main(void) { return d; }\n')
    assert r.returncode == 1
    assert "e.c:1: initializer element is not constant" in r.stderr
    r = compile_c(tmp_path, 'char *p = &"xy"[1];\nint main(void) { return 0; }\n')
    assert r.returncode == 0, r.stderr


# ---- the line of a diagnostic -----------------------------------------------

def test_a_block_comment_with_splices_keeps_the_line(tmp_path):
    r = compile_c(tmp_path, "int main(void){\n int a = 1 /* c \\\n c2 \\\n"
                  " c3 */ + 1;\n return zz;\n}\n")
    assert "e.c:5: 'zz' undeclared" in r.stderr


def test_a_line_comment_that_ends_in_a_backslash_goes_on(tmp_path):
    r = compile_c(tmp_path, "// c \\\ncontinued\nint main(void){\n  return zz;\n}\n")
    assert "e.c:4: 'zz' undeclared" in r.stderr


def test_line_in_an_include_file(tmp_path):
    (tmp_path / "inc.h").write_text(
        'int i1;\nint i2;\nint i3;\n#line 500 "z.h"\nint i(void){ return yy; }\n')
    r = compile_c(tmp_path, '#include "inc.h"\nint main(void){ return i(); }\n')
    assert "z.h:500: 'yy' undeclared" in r.stderr


def test_line_with_a_hex_number_is_rejected(tmp_path):
    r = compile_c(tmp_path, "#line 0x10\nint main(void){ return 0; }\n")
    assert r.returncode == 1
    assert "e.c:1: #line needs a decimal line number" in r.stderr


def test_an_error_at_the_end_of_the_input_names_the_file_and_line(tmp_path):
    r = compile_c(tmp_path, "int main(void){\n  int x = 1;\n  x +\n")
    assert r.returncode == 1
    assert "unexpected end of input at e.c:3" in r.stderr
