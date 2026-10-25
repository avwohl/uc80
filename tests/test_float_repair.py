"""Runtime tests for bugs found while verifying the float strength fix.

Each program is compiled, run under cpmemu, and compared with the value
a host C compiler gives (written out here as the expected text).
"""

import itertools

import pytest

from tests.test_struct_return_abi import build_and_run
from tests.test_struct_return_abi import pytestmark  # noqa: F401


def test_typeof_of_object_declared_with_typeof(tmp_path):
    src = r'''
int printf(const char *, ...);
typeof(unsigned char) g = 7;
typeof(g) g2 = 9;
const char kc = 3;
int main(void) {
    typeof(unsigned char) a = 200;
    typeof(a) b = a + 100;
    typeof(b) c;
    typeof(volatile unsigned char) v = 200;
    typeof(v) w = v + 100;
    typeof_unqual(kc) ku;
    typeof_unqual(g2) gu;
    printf("%d %d %d %d\n", b, (int)sizeof(b), (int)sizeof(c), (int)sizeof(g2));
    printf("%d %d %d %d\n", w, (int)sizeof(w), (int)sizeof(ku), (int)sizeof(gu));
    return 0;
}
'''
    assert build_and_run(tmp_path, src).split("\n")[:2] == [
        "44 1 1 1", "44 1 1 1"]


def test_nested_shift_wider_than_operand_is_zero(tmp_path):
    src = r'''
int printf(const char *, ...);
int main(void) {
    long long y = 1; unsigned long long u = 3; long long n = -1;
    unsigned int ui = 0x8001u; int k = 3;
    printf("%d %d %d\n", (int)((y << 33) << 33), (int)((u << 40) << 30),
           (int)((n >> 40) >> 30));
    printf("%d %d\n", (int)((ui << 9) << 9), (int)((ui >> 9) >> 9));
    printf("%ld %ld\n", (long)((k << 2) << 3), (long)((y << 20) << 20));
    return 0;
}
'''
    out = build_and_run(tmp_path, src).split("\n")
    assert out[0] == "0 0 -1"
    assert out[1] == "0 0"
    assert out[2].startswith("96 ")


def test_negative_float_ordering(tmp_path):
    vals = [-1e9, -44.0, -1.0, -0.5, 0.0, 0.5, 1.0, 44.0, 1e9]
    names = []
    decls = []
    for i, v in enumerate(vals):
        decls.append(f"    volatile TYPE a{i} = {v!r};")
        names.append(f"a{i}")
    lines = []
    want = []
    for (i, x), (j, y) in itertools.product(enumerate(vals), repeat=2):
        for op, f in (("<", x < y), (">", x > y), ("<=", x <= y),
                      (">=", x >= y), ("==", x == y), ("!=", x != y)):
            lines.append(f'    P(a{i} {op} a{j});')
            want.append(str(int(f)))
    for typ in ("float", "double"):
        body = "\n".join(decls).replace("TYPE", typ) + "\n" + "\n".join(lines)
        src = ("int printf(const char *, ...);\n"
               "#define P(e) printf(\"%d\\n\", (e))\n"
               "int main(void) {\n" + body + "\n    return 0;\n}\n")
        out = build_and_run(tmp_path, src, name=typ).split("\n")
        assert out[:len(want)] == want, typ


def test_float_through_pointer_to_pointer(tmp_path):
    src = r'''
int printf(const char *, ...);
float gf = 1.5f; float *pf = &gf; float **qf = &pf; float ***rf = &qf;
double gd = 2.5; double *pd = &gd; double **qd = &pd;
long gl = 100000L; long *pl = &gl; long **ql = &pl;
char gc = 'x'; char *pc = &gc; char **qc = &pc; char ***rc = &qc;
int main(void) {
    float x = **qf; double y = **qd; long z = **ql; float w = ***rf;
    printf("%d %d %ld %d\n", (int)(x * 10), (int)(y * 10), z, (int)(w * 10));
    printf("%d %d %c %c\n", (int)(**qf * 10), (int)(**qd * 10), **qc, ***rc);
    return 0;
}
'''
    assert build_and_run(tmp_path, src).split("\n")[:2] == [
        "15 25 100000 15", "15 25 x x"]


@pytest.mark.parametrize("flags", [(), ("--no-inlining",)])
def test_inlined_char_typedef_function_narrows(tmp_path, flags):
    src = r'''
int printf(const char *, ...);
typedef signed char I8;
typedef unsigned char U8;
typedef unsigned int UI;
I8 sb[4] = {21, 17, -100, 127};
I8 f3(I8 c) { return c * 3; }
I8 fa(I8 c) { return c + 100; }
I8 fs(I8 c) { return c * 8; }
U8 fu(U8 c) { return c * 5; }
UI fw(UI c) { return c * 40000u; }
int main(void) {
    int i;
    for (i = 0; i < 4; i++) {
        I8 s = sb[i];
        printf("%ld %ld %ld %ld %lu\n", (long)f3(s), (long)fa(s), (long)fs(s),
               (long)fu((U8)s), (unsigned long)fw(s));
    }
    return 0;
}
'''
    out = build_and_run(tmp_path, src, flags=flags).split("\n")[:4]
    def s8(v):
        v &= 255
        return v - 256 if v > 127 else v
    want = []
    for s in (21, 17, -100, 127):
        want.append(f"{s8(s*3)} {s8(s+100)} {s8(s*8)} {(s & 255) * 5 & 255} "
                    f"{(s & 0xFFFF) * 40000 & 0xFFFF}")
    assert out == want
