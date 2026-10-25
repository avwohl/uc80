"""Integer-only rewrites must not touch a floating operand; typeof; extern.

The AST optimizer turned ``x * 2^n`` into ``x << n`` without knowing the
type of ``x``: ``float nv = 1.5f; nv * 4`` was 1.5 (a shift of the float's
integer value, which then converted back).  ``x / 8u`` became ``x >> 3``
in uc_core, ``(x * 3) * 5`` folded to a wrapped integer constant, ``x - x``
became the int 0.  Every program here is compiled, run under cpmemu, and
compared with the exact value (the operands are powers of two times short
fractions, so float, double and long double results are exact).

The same file holds the tests of two bugs found with these programs:
``typeof`` of an object (char is one byte) and of a type name with
qualifiers, and a block-scope ``extern`` object that must be an EXTRN.
"""

import subprocess
import sys

import pytest

from tests.test_struct_return_abi import (CPMEMU, LIB_DIR, REPO,  # noqa: F401
                                          build_and_run)
from tests.test_struct_return_abi import pytestmark  # noqa: F401
from tests.test_separate_compilation_bss import _build_two_units

VALUES = {"gx": 1.5, "gy": -2.75, "gz": 100000.0}


def trunc(v):
    return int(v)           # C converts a float to long by truncation


def power_cases(types_, powers):
    """(C statements, expected output lines) for one type."""
    c, want = [], []
    for n in powers:
        p = 1 << n
        for var, val in VALUES.items():
            forms = [
                (f"{var} * {p}", val * p),
                (f"{p} * {var}", val * p),
                (f"{var} * {p}u", val * p),
                (f"{var} * {p}UL", val * p),
                (f"{var} * {p}L", val * p),
                (f"{var} / {p} * 16384", val / p * 16384),
                (f"{var} / {p}u * 16384", val / p * 16384),
                (f"{var} / {p}L * 16384", val / p * 16384),
            ]
            for text, v in forms:
                c.append(f"    P(({types_}) ({text}));")
                want.append(trunc(v))
            # compound assignment: *= and /= on a local copy
            c.append(f"    {{ {types_} t = {var}; t *= {p}; P(t); }}")
            want.append(trunc(val * p))
            c.append(f"    {{ {types_} t = {var}; t /= {p}; P(t * 16384); }}")
            want.append(trunc(val / p * 16384))
            c.append(f"    {{ {types_} t = {var}; t = t * {p}; P(t); }}")
            want.append(trunc(val * p))
            c.append(f"    {{ {types_} t = {var}; t = t / {p}u; P(t * 16384); }}")
            want.append(trunc(val / p * 16384))
    return c, want


def program(ctype, powers):
    body, want = power_cases(ctype, powers)
    src = f"""#include <stdio.h>
{ctype} gx = 1.5, gy = -2.75, gz = 100000;
#define P(e) printf("%ld\\n", (long)(e))
int main(void) {{
{chr(10).join(body)}
    return 0;
}}
"""
    return src, want


def run_lines(tmp_path, src):
    out = build_and_run(tmp_path, src)
    return [int(x) for x in out.split()]


@pytest.mark.parametrize("ctype", ["float", "double", "long double"])
@pytest.mark.parametrize("powers", [range(1, 8), range(8, 15)],
                         ids=["2..2^7", "2^8..2^14"])
def test_power_of_two_multiply_divide_of_a_float(tmp_path, ctype, powers):
    src, want = program(ctype, powers)
    assert run_lines(tmp_path, src) == want


def test_the_reported_cases(tmp_path):
    out = build_and_run(tmp_path, """#include <stdio.h>
float nv = 1.5f;
int main(void) {
    float r = nv * 4;
    double d = 2.5;
    d = d * 8;
    printf("%d %d\\n", (int)(r * 10), (int)(d * 10));
    return 0;
}
""")
    assert out.split() == ["60", "200"]


HIDDEN = """#include <stdio.h>
typedef double real;
struct S { float f; int i; };
real tr = -2.75;
float arr[2] = {1.5f, 3.0f};
real fn(void) { return 5.5; }
long double ld = 1.5L;
int main(void) {
    struct S s;
    struct S *ps = &s;
    float *p = arr;
    real k = tr;
    int zero = 0;
    s.f = 1.5f; s.i = 3;
    printf("%ld\\n", (long)(s.f * 16 * 10));          /* member */
    printf("%ld\\n", (long)(ps->f * 16 * 10));        /* arrow member */
    printf("%ld\\n", (long)(*p * 32 * 10));           /* dereference */
    printf("%ld\\n", (long)(arr[1] * 64 * 10));       /* element */
    printf("%ld\\n", (long)(k * 128 * 10));           /* typedef */
    printf("%ld\\n", (long)(fn() * 256 * 10));        /* call */
    printf("%ld\\n", (long)(ld * 8 * 10));           /* long double */
    printf("%ld\\n", (long)((s.f / 4u) * 100));      /* unsigned divisor */
    printf("%ld\\n", (long)((zero ? s.f : k) * 4 * 10)); /* conditional */
    printf("%ld\\n", (long)(((float)s.i) * 8));       /* cast */
    printf("%ld\\n", (long)((s.f * 3) * 5 * 10));     /* nested constants */
    printf("%ld\\n", (long)((k + 70000) + 70000));    /* nested, wraps in 16 bits */
    printf("%ld\\n", (long)(s.f - s.f));
    printf("%ld\\n", (long)(s.f * 0 + 7));
    printf("%d\\n", s.i * 8);                          /* integers still shift */
    return 0;
}
"""


def test_float_reached_through_other_names(tmp_path):
    want = [240, 240, 480, 1920, -3520, 14080, 120, 37, -110, 24, 225,
            139997, 0, 7, 24]
    assert run_lines(tmp_path, HIDDEN) == want


def test_integer_multiply_and_divide_still_use_shifts(tmp_path):
    c_file = tmp_path / "i.c"
    c_file.write_text("""int f(int x) { return x * 8; }
unsigned g(unsigned x) { return x / 8u; }
long h(long x) { return x * 4; }
int main(void) { return f(3) + g(64) + (int)h(1); }
""")
    mac = tmp_path / "i.mac"
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "--no-whole-program", "-o", str(mac)],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    text = mac.read_text()
    assert "__mul" not in text and "__div" not in text, text


# ----------------------------------------------------------------- typeof

def test_typeof_of_an_object_has_the_size_of_the_object(tmp_path):
    out = build_and_run(tmp_path, """#include <stdio.h>
char gc = 5; volatile unsigned char vu = 7; long gl; struct S { int a; char b; } gs;
typeof(gc) g2;
typeof(gs) g3;
struct T { typeof(gc) m; typeof(gl) n; };
int main(void) {
    char c; int i;
    typeof(c) a; typeof(vu) b; typeof(gl) l; typeof(gs) s; typeof(&c) p;
    typeof(gs.b) m; typeof(c) arr[3]; typeof_unqual(vu) u;
    printf("%d %d %d %d %d %d %d %d %d %d %d\\n", (int)sizeof(a),
           (int)sizeof(b), (int)sizeof(l), (int)sizeof(s), (int)sizeof(p),
           (int)sizeof(m), (int)sizeof(arr), (int)sizeof(g2),
           (int)sizeof(g3), (int)sizeof(struct T), (int)sizeof(typeof(i)));
    a = 300; l = 70000L;
    printf("%d %ld\\n", a, l);
    return 0;
}
""")
    assert out.split("\n")[:2] == ["1 1 4 3 2 1 3 1 3 5 2", "44 70000"]


def test_typeof_of_a_type_name_with_qualifiers(tmp_path):
    out = build_and_run(tmp_path, """#include <stdio.h>
typedef unsigned char MyU;
struct S { int a; long b; };
int main(void) {
    typeof(MyU) a; typeof(*(volatile unsigned char *)0) v; typeof(MyU *) p;
    typeof(struct S) s; typeof(long long) ll; typeof(unsigned char [4]) arr;
    typeof(int *[3]) ap; typeof(const char *) cp; typeof_unqual(volatile int) ui;
    typeof(volatile unsigned char) vb; typeof(const volatile int) cvi = 3;
    __typeof__(short) sh;
    a = 200; ll = 5; arr[3] = 9; sh = -2;
    printf("%d %d %d %d %d %d %d %d %d %d %d %d\\n", (int)sizeof(a),
           (int)sizeof(v), (int)sizeof(p), (int)sizeof(s), (int)sizeof(ll),
           (int)sizeof(arr), (int)sizeof(ap), (int)sizeof(cp),
           (int)sizeof(ui), (int)sizeof(sh), (int)sizeof(vb), (int)sizeof(cvi));
    printf("%d %d %d\\n", a + 100, arr[3], sh);
    return 0;
}
""")
    assert out.split("\n")[:2] == ["1 1 2 6 8 4 6 2 2 2 1 2",
                                   "300 9 -2"]


# ----------------------------------------------------------------- extern

def test_block_scope_extern_object_is_an_external_symbol(tmp_path):
    unit_a = """void f(void) { extern int g; g = 1; }
int h(void) {
    extern int cnt; extern char arr[4]; extern int fn2(int);
    return cnt + arr[1] + fn2(2);
}
"""
    unit_b = """#include <stdio.h>
int g = 0; int cnt = 40; char arr[4] = {1, 2, 3, 4};
int fn2(int x) { return x * 100; }
void f(void); int h(void);
int main(void) { f(); printf("%d %d\\n", g, h()); return 0; }
"""
    assert "1 242\n" in _build_two_units(tmp_path, unit_a, unit_b)
