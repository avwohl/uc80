"""Constraint errors that the compiler gives before it generates code.

  - an undeclared name is an error with file:line (C23 has no implicit
    declarations), for variables and for functions;
  - ++, --, = and op= on a const object are errors (6.5.16, 6.5.2.4,
    6.5.3.1), also through const-qualified lvalues;
  - a variable length array is an error (__STDC_NO_VLA__ is 1);
  - a two-file --no-whole-program program builds and runs when crt0.rel is
    linked first.

The error tests check the exact file:line.  The run tests execute the code.
"""

import subprocess
import sys

import pytest

from tests.test_struct_return_abi import CPMEMU, LIB_DIR, REPO, pytestmark  # noqa: F401


def compile_c(tmp_path, files, flags=(), name="main"):
    paths = []
    for fname, text in files.items():
        (tmp_path / fname).write_text(text)
        if fname.endswith(".c"):
            paths.append(fname)
    return subprocess.run(
        [sys.executable, "-m", "uc80.main", *paths, *flags, "-o",
         str(tmp_path / (name + ".mac"))],
        capture_output=True, text=True, cwd=str(tmp_path),
        env={**__import__("os").environ,
             "PYTHONPATH": str(REPO / "src") + ":" + __import__("os").environ.get("PYTHONPATH", "")})


def run_program(tmp_path, units, flags=()):
    """Compile each unit alone (--no-whole-program), link with crt0.rel
    first, run under cpmemu, return the program output."""
    rels = []
    for fname, text in units.items():
        r = compile_c(tmp_path, {fname: text}, ["--no-whole-program",
                      "--printf", "int", *flags], name=fname[:-2])
        assert r.returncode == 0, r.stderr
        mac = tmp_path / (fname[:-2] + ".mac")
        rel = tmp_path / (fname[:-2] + ".rel")
        subprocess.run(["um80", str(mac), "-o", str(rel)], check=True,
                       capture_output=True, cwd=str(tmp_path))
        rels.append(str(rel))
    com = tmp_path / "prog.com"
    subprocess.run(["ul80", str(LIB_DIR / "crt0.rel"), *rels,
                    str(LIB_DIR / "libc.lib"), str(LIB_DIR / "runtime.lib"),
                    "-o", str(com)], check=True, capture_output=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         text=True, timeout=60, cwd=str(tmp_path))
    return [ln for ln in run.stdout.replace("\r", "").split("\n")
            if not ln.startswith(("CPU mode", "Loaded ", "Program exit",
                                  "Note:"))]


def errors_of(r):
    return [ln for ln in r.stderr.splitlines() if ln.startswith("uc80: error")]


# ----- undeclared names -------------------------------------------------

def test_variable_outside_its_block_is_an_error_with_file_and_line(tmp_path):
    r = compile_c(tmp_path, {
        "hdr.h": "/* header */\n#define ONE 1\nint hf(void);\n",
        "u.c": '#include "hdr.h"\n\nint main(void)\n{\n    {\n'
               '        int d6 = ONE;\n        (void)d6;\n    }\n'
               '    return d6;\n}\n'})
    assert r.returncode == 1
    errs = errors_of(r)
    assert len(errs) == 1, r.stderr
    assert errs[0].startswith("uc80: error: u.c:9: 'd6' undeclared"), errs


def test_assignment_to_undeclared_name_is_an_error(tmp_path):
    r = compile_c(tmp_path, {"u.c": "int main(void)\n{\n    d = 4;\n"
                                    "    return sizeof(d);\n}\n"})
    assert r.returncode == 1
    assert errors_of(r)[0].startswith("uc80: error: u.c:3: 'd' undeclared")


def test_unused_function_is_checked_too(tmp_path):
    r = compile_c(tmp_path, {"u.c": "static int unused(void)\n{\n"
                                    "    return zz;\n}\nint main(void){return 0;}\n"})
    assert r.returncode == 1
    assert errors_of(r)[0].startswith("uc80: error: u.c:3: 'zz' undeclared")


def test_call_without_declaration_is_an_error(tmp_path):
    r = compile_c(tmp_path, {"u.c": "int main(void)\n{\n    return foo(3);\n}\n"})
    assert r.returncode == 1
    e = errors_of(r)
    assert e[0].startswith("uc80: error: u.c:3: implicit declaration of "
                           "function 'foo'"), e


def test_option_makes_implicit_function_a_warning(tmp_path):
    r = compile_c(tmp_path, {"u.c": "int main(void)\n{\n    return foo(3);\n}\n"},
                  ["--allow-implicit-functions"])
    assert r.returncode == 0, r.stderr
    assert "u.c:3: warning: implicit declaration of function 'foo'" in r.stderr


def test_valid_scopes_are_not_reported(tmp_path):
    src = r'''
#include <stdio.h>
#include <stddef.h>
typedef struct node { int v; struct node *next; } node;
enum { A = 3, B = A + 1 };
static int tab[B];
int fwd(int);
struct pt { int x, y; };
int use(node *n) { return n->v + offsetof(struct pt, y) + sizeof(tab); }
int main(void)
{
    node n = { 5, NULL };
    int i, s = 0;
    for (int k = 0; k < B; k++) { int t = k * 2; tab[k] = t; s += t; }
    { int fwd2(int); (void)fwd2; }
    i = fwd(2) + use(&n) + (int)sizeof(struct pt) + __LINE__ * 0;
    printf("%d %d %s\n", s, i, __func__);
    goto done;
done:
    return 0;
}
int fwd(int v) { return v + A; }
'''
    r = compile_c(tmp_path, {"u.c": src})
    assert r.returncode == 0, r.stderr


# ----- const modification -------------------------------------------------

PRE = ("#include <stdint.h>\nstruct S { int m; const int c; int a[2]; };\n"
       "struct T { const int c; };\nstruct U { struct T t; };\n"
       "int g(void);\nconst int *gp(void);\n")

CASES = [
    ("volatile uint8_t *const p = (volatile uint8_t *)0x8000; *p--;",
     "decrement of read-only variable 'p'"),
    ("int *const p = 0; p++;", "increment of read-only variable 'p'"),
    ("int *const p = 0; ++p;", "increment of read-only variable 'p'"),
    ("const int x = 1; x = 2;", "assignment of read-only variable 'x'"),
    ("const int x = 1; x += 2;", "assignment of read-only variable 'x'"),
    ("const int x = 1; x <<= 2;", "assignment of read-only variable 'x'"),
    ("const int x = 1; x--;", "decrement of read-only variable 'x'"),
    ("const struct S s = {0}; s.m = 1;", "read-only member 'm'"),
    ("struct S s = {0}; s.c = 1;", "read-only member 'c'"),
    ("struct S s = {0}; struct S *p = &s; p->c++;", "read-only member 'c'"),
    ("struct S s = {0}; const struct S *p = &s; p->m = 1;",
     "read-only member 'm'"),
    ("const struct S s = {0}; s.a[1] = 1;", "read-only location"),
    ("const int *p = 0; *p = 1;", "read-only location '*p'"),
    ("const int a[2] = {1, 2}; a[0] = 1;", "read-only location"),
    ("int x; *(const int *)&x = 1;", "read-only location"),
    ("*gp() = 1;", "read-only location"),
    ("struct T a = {0}, b = {1}; a = b;", "structure with a read-only member"),
    ("struct U a = {{0}}, b = {{1}}; a = b;",
     "structure with a read-only member"),
    ("const int *const p = 0; p++;", "increment of read-only variable 'p'"),
]


@pytest.mark.parametrize("body,fragment", CASES)
def test_modifying_a_const_object_is_an_error(tmp_path, body, fragment):
    src = PRE + "void f(void)\n{\n    " + body + "\n}\n"
    r = compile_c(tmp_path, {"c.c": src})
    assert r.returncode == 1, r.stderr
    line = PRE.count("\n") + 3
    e = errors_of(r)
    assert len(e) == 1 and e[0].startswith(f"uc80: error: c.c:{line}: "), e
    assert fragment in e[0], e


def test_const_parameter_and_through_typedef(tmp_path):
    src = ("typedef const int CI;\nvoid f(const int n, CI *q)\n{\n"
           "    n++;\n    *q = 1;\n}\n")
    r = compile_c(tmp_path, {"c.c": src})
    e = errors_of(r)
    assert r.returncode == 1 and len(e) == 2, e
    assert "c.c:4: " in e[0] and "c.c:5: " in e[1], e


def test_legal_modifications_compile_and_run(tmp_path):
    src = r'''
#include <stdio.h>
struct S { int m; const int c; int a[2]; };
struct V { int *const p; int v; };
static int arr[3] = {1, 2, 3};
int main(void)
{
    const int *p = arr;
    int *const q = arr;
    struct S s = { 1, 2, { 3, 4 } };
    const struct S *cs = &s;
    struct V v = { arr, 7 };
    const char *msg = "abc";
    int n = 0;
    p++;
    *q = 10;
    s.m = 5;
    s.a[1] += 6;
    v.v++;
    *v.p = 11;
    msg += 2;
    n += *p + *q + s.m + s.a[1] + cs->c + v.v + arr[0] + (int)*msg;
    printf("%d %d %d %d\n", n, *p, s.a[1], arr[0]);
    return 0;
}
'''
    assert run_program(tmp_path, {"p.c": src})[0] == "148 2 10 11"


# ----- variable length arrays ---------------------------------------------

def test_vla_is_an_error_and_macro_says_so(tmp_path):
    src = ("int f(int k)\n{\n    short v[k + 2];\n    v[0] = 1;\n"
           "    return v[0];\n}\n")
    r = compile_c(tmp_path, {"v.c": src})
    assert r.returncode == 1
    e = errors_of(r)
    assert e[0].startswith("uc80: error: v.c:3: variable length array"), e
    assert "__STDC_NO_VLA__" in e[0]


def test_stdc_no_vla_is_one_and_constant_arrays_still_work(tmp_path):
    src = r'''
#include <stdio.h>
enum { N = 3 };
int main(void)
{
    int a[N * 2], b[sizeof(long)], c[1 ? 4 : 5];
    static int d[N + 1];
    a[5] = 1; b[0] = 2; c[3] = 3; d[3] = 4;
#if defined(__STDC_NO_VLA__) && __STDC_NO_VLA__ == 1
    printf("novla %d\n", a[5] + b[0] + c[3] + d[3]);
#endif
    return 0;
}
'''
    assert run_program(tmp_path, {"p.c": src})[0] == "novla 10"


# ----- --no-whole-program ---------------------------------------------------

TWO_A = r'''
#include <stdio.h>
extern int counter;
int bump(int);
static int local = 3;
const char *name = "two files";
int main(void)
{
    printf("%s %d %d\n", name, bump(4), local);
    printf("%d\n", counter);
    return 0;
}
'''
TWO_B = "int counter = 40;\nint zero;\nint bump(int v) { counter += v; return counter + zero; }\n"


def test_two_file_no_whole_program_program_runs(tmp_path):
    out = run_program(tmp_path, {"a.c": TWO_A, "b.c": TWO_B})
    assert out[:2] == ["two files 44 3", "44"], out


def test_main_module_warns_about_missing_startup_code(tmp_path):
    r = compile_c(tmp_path, {"a.c": TWO_A}, ["--no-whole-program",
                                             "--printf", "int"])
    assert r.returncode == 0
    assert "link crt0.rel first" in r.stderr
    r = compile_c(tmp_path, {"b.c": TWO_B}, ["--no-whole-program"])
    assert "crt0.rel" not in r.stderr
    r = compile_c(tmp_path, {"a.c": TWO_A}, ["--no-whole-program",
                  "--printf", "int", "--no-embed-startup"])
    assert "crt0.rel" not in r.stderr
