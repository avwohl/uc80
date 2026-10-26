"""Call and frame overhead: no IX frame, cheaper argument cleanup, and
byte-wise switch dispatch.

  * A function whose generated body never names IX (no parameter read, no
    IX local, no stack frame, no IX in inline asm) has no
    ``push IX / ld IX,0 / add IX,SP`` and no ``ld SP,IX / pop IX``.
  * After a call that returns a long or float, up to five words of
    arguments are dropped with ``pop BC`` (DEHL stays in place); after a
    long long, one or two words with ``pop DE``.
  * ``switch`` on a char compares bytes in A (case values outside the
    char's range are dropped); on a 16-bit int whose case values share a
    high byte, H is tested once and then bytes in A; otherwise each case
    tests the low byte and only then the high byte.  A dense switch uses a
    jump table with an 8-bit range check in the first two forms.

The unit tests call generate() directly and pin the shape; the end-to-end
tests build programs, run them under cpmemu and compare every value with
a Python model.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from uc_core.frontend import parse as _frontend_parse
from uc80.codegen import generate

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"

needs_toolchain = pytest.mark.skipif(
    not CPMEMU.exists() or subprocess.run(
        ["which", "um80"], capture_output=True).returncode != 0,
    reason="cpmemu or um80 not available")


def gen(source: str) -> str:
    return generate(_frontend_parse(source, "<test>"), enable_inlining=False,
                    enable_const_propagation=False,
                    enable_dead_elimination=False,
                    register_cc=False)


def body(code: str, name: str) -> str:
    start = code.index(f"\n_{name}:")
    end = code.index(f"@{name}_ret:", start)
    return code[start:end]


def func(code: str, name: str) -> str:
    start = code.index(f"\n_{name}:")
    end = code.index("\n\tret", start)
    return code[start:end + 5]


# ---------------------------------------------------------------- frames

class TestFrameElision:
    def test_leaf_without_parameters_has_no_frame(self):
        code = gen("int g; void f(void) { g = 5; }")
        text = func(code, "f")
        assert "IX" not in text
        assert text.rstrip().endswith("ret")

    def test_function_with_a_call_and_no_ix_has_no_frame(self):
        code = gen("void h(int); void f(void) { h(1); h(2); }")
        assert "IX" not in func(code, "f")

    def test_parameter_read_keeps_the_frame(self):
        text = func(gen("int f(int a) { return a; }"), "f")
        assert "push\tIX" in text and "pop\tIX" in text
        # F8: with no stack locals SP is IX already at the return.
        assert "ld\tSP,IX" not in text

    def test_stack_locals_keep_the_stack_pointer_restore(self):
        text = func(gen("int f(int n) { int a[4]; a[0] = n; "
                        "return n ? f(n - 1) + a[0] : 0; }"), "f")
        assert "ld\tSP,IX" in text and "pop\tIX" in text

    def test_stack_local_keeps_the_frame(self):
        text = func(gen("int g(int *); int f(void) { int a; g(&a); return a; }"
                        ), "f")
        # Either shared storage (no IX at all) or a real frame; never half.
        assert ("push\tIX" in text) == ("pop\tIX" in text)
        assert ("push\tIX" in text) == ("ld\tSP,IX" in text)

    def test_unused_parameter_gives_no_frame(self):
        assert "IX" not in func(gen("int f(int a) { return 3; }"), "f")

    def test_inline_asm_naming_ix_keeps_the_frame(self):
        text = func(gen('void f(void) { __asm__("push ix\\n\\tpop hl"); }'),
                    "f")
        assert "push\tIX" in text and "ld\tSP,IX" in text

    def test_statement_expression_keeps_the_frame(self):
        text = func(gen("int g(void); int f(void) "
                        "{ return ({ int t = g(); t; }); }"), "f")
        assert "push\tIX" in text and "ld\tSP,IX" in text

    def test_variadic_keeps_the_frame(self):
        text = func(gen("int f(int n, ...) { return n; }"), "f")
        assert "push\tIX" in text


# --------------------------------------------------------------- cleanup

class TestCleanup:
    def test_long_return_pops_into_bc(self):
        code = gen("long g(int, int, int); long f(void) { return g(1, 2, 3); }")
        text = func(code, "f")
        assert text.count("pop\tBC") == 3
        assert "ex\tDE,HL" not in text

    def test_long_return_big_cleanup_still_saves_dehl(self):
        code = gen("long g(int,int,int,int,int,int); "
                   "long f(void) { return g(1,2,3,4,5,6); }")
        text = func(code, "f")
        assert "pop\tBC" not in text
        assert "add\tHL,SP" in text

    def test_int_return_cleanup_unchanged(self):
        code = gen("int g(int, int); int f(void) { return g(1, 2); }")
        assert func(code, "f").count("pop\tDE") == 2

    def test_long_long_return_pops_two_words(self):
        code = gen("long long g(int, int); "
                   "long long f(void) { return g(1, 2); }")
        text = func(code, "f")
        assert text.count("pop\tDE") == 2
        assert "add\tHL,SP" not in text


# ---------------------------------------------------------------- switch

class TestSwitchShapes:
    def test_char_switch_compares_bytes(self):
        code = gen("char c; int f(void) { switch (c) { case 1: return 10; "
                   "case 5: return 11; case 9: return 12; } return 0; }")
        text = body(code, "f")
        assert "cp\t5" in text and "sbc\tHL,DE" not in text
        assert "ld\tH,0" not in text.split("cp\t1")[0]

    def test_char_case_outside_the_range_is_dropped(self):
        code = gen("unsigned char c; int f(void) { switch (c) { case 1: "
                   "return 10; case 300: return 11; case -1: return 12; } "
                   "return 0; }")
        text = body(code, "f")
        assert "cp\t1" in text
        assert "cp\t44" not in text and "cp\t255" not in text

    def test_int_switch_with_one_high_byte_tests_h_once(self):
        code = gen("int x; int f(void) { switch (x) { case 0x1201: "
                   "return 1; case 0x1207: return 2; } return 0; }")
        text = body(code, "f")
        assert text.count("ld\tA,H") == 1 and "cp\t18" in text
        assert "cp\t7" in text and "sbc\tHL,DE" not in text

    def test_int_switch_with_mixed_high_bytes_tests_low_first(self):
        code = gen("int x; int f(void) { switch (x) { case 1: return 1; "
                   "case 0x201: return 2; } return 0; }")
        text = body(code, "f")
        assert "jr\tNZ,@SWNEXT" in text and "sbc\tHL,DE" not in text

    def test_dense_char_switch_uses_an_8_bit_table(self):
        code = gen("unsigned char c; int f(void) { switch (c) { case 0: "
                   "return 1; case 1: return 2; case 2: return 3; case 3: "
                   "return 4; case 4: return 5; } return 0; }")
        text = body(code, "f")
        assert "SWTAB" in text and "cp\t5" in text
        assert "sbc\tHL,DE" not in text

    def test_long_switch_value_tests_all_four_bytes(self):
        # F7: the 16-bit chain compared HL alone, so case 65536 matched 0.
        code = gen("long x; int f(void) { switch (x) { case 1: return 1; "
                   "case 2: return 2; } return 0; }")
        text = body(code, "f")
        assert "cp\t1" in text and "cp\t2" in text
        assert "ld\tA,D" in text and "ld\tA,E" in text


# ------------------------------------------------------------ end to end

def build_and_run(tmp_path, source, name="prog"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text("#include <stdio.h>\n" + source)
    mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "--printf", "long", "-o", str(mac)],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    env = dict(os.environ, PYTHONHASHSEED="0")
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=120, env=env)
    out = run.stdout.decode("latin-1").replace("\r", "")
    return [l.strip() for l in out.splitlines()
            if l.strip() and not l.startswith(("CPU mode", "Loaded",
                                               "Program exit"))]


@needs_toolchain
def test_frameless_and_framed_functions_return_correct_values(tmp_path):
    src = r'''
int g1, g2;
void setg(void) { g1 = 7; g2 += 3; }
void twice(void) { setg(); setg(); }
int fwithparam(int a, int b) { return a - b; }
int unused(int a, int b) { return 5; }
int rec(int n) { return n ? n + rec(n - 1) : 0; }
long l5(int a, int b, int c, int d, int e) {
    return (long)a * 100000L + b * 1000 + c * 100 + d * 10 + e;
}
long l1(int a) { return 70000L + a; }
long l6(int a, int b, int c, int d, int e, int f) {
    return 1000000L * f + a + b + c + d + e;
}
long long q1(int a) { return 5000000000LL + a; }
long long q2(int a, int b) { return 5000000000LL + a * b; }
long long q3(int a, int b, int c) { return 5000000000LL + a * b * c; }
int main(void) {
    int i;
    long s = 0;
    twice();
    printf("%d %d\n", g1, g2);
    printf("%d %d %d\n", fwithparam(9, 4), unused(1, 2), rec(10));
    for (i = 0; i < 3; i++) {
        s += l1(i) + l5(1, 2, 3, 4, i) + l6(1, 2, 3, 4, 5, i);
        printf("%ld %ld %ld\n", l1(i), l5(i, 2, 3, 4, 5), l6(i, 2, 3, 4, 5, 6));
    }
    printf("%ld\n", s);
    printf("%ld %ld %ld\n", (long)(q1(3) % 1000), (long)(q2(3, 4) % 1000),
           (long)(q3(2, 3, 4) % 1000));
    return 0;
}
'''
    out = build_and_run(tmp_path, src)
    s = 0
    for i in range(3):
        s += 70000 + i + (1 * 100000 + 2000 + 300 + 40 + i) + (1000000 * i + 15)
    lines = ["7 6", "5 5 55"]
    for i in range(3):
        lines.append(f"{70000 + i} {i * 100000 + 2000 + 300 + 40 + 5} "
                     f"{6000000 + i + 14}")
    # q: 5000000003 % 1000 = 3, 5000000012 -> 12, 5000000024 -> 24
    lines += [str(s), "3 12 24"]
    assert out == lines


def to_c(v: int, signed: bool, bits: int) -> int:
    v &= (1 << bits) - 1
    if signed and v >> (bits - 1):
        v -= 1 << bits
    return v


SWITCH_SRC = r'''
int sc_sw(signed char c) {
    switch (c) {
    case -128: return 1;
    case -1: return 2;
    case 0: return 3;
    case 5: return 4;
    case 127: return 5;
    case 200: return 6;
    case -300: return 7;
    default: return -1;
    }
}
int uc_sw(unsigned char c) {
    switch (c) {
    case 0: return 1;
    case 1: return 2;
    case 2: case 3: return 3;
    case 4: return 4;
    case 6: return 5;
    case 255: return 6;
    case -1: return 7;
    case 256: return 8;
    default: return -1;
    }
}
int uc_dense(unsigned char c) {
    switch (c) {
    case 250: return 1;
    case 251: return 2;
    case 252: return 3;
    case 254: return 4;
    case 255: return 5;
    }
    return 0;
}
int sc_dense(signed char c) {
    switch (c) {
    case -3: return 1;
    case -2: return 2;
    case -1: return 3;
    case 0: return 4;
    case 2: return 5;
    }
    return 0;
}
int sc_wide(signed char c) {
    int r = 0;
    switch (c) {
    case -128: r += 1;
    case -127: r += 2; break;
    case 126: r += 4;
    case 127: r += 8; break;
    case -64: case -63: case -62: r = 100; break;
    }
    return r;
}
int hi_same(int x) {
    switch (x) {
    case 0x1201: return 1;
    case 0x1207: return 2;
    case 0x12FF: return 3;
    case 0x1200: return 4;
    default: return -1;
    }
}
int hi_dense(int x) {
    switch (x) {
    case 0x0300: return 1;
    case 0x0301: return 2;
    case 0x0302: return 3;
    case 0x0303: return 4;
    case 0x0305: return 5;
    }
    return 0;
}
int hi_neg(int x) {
    switch (x) {
    case -1: return 1;
    case -2: return 2;
    case -256: return 3;
    }
    return 0;
}
int hi_mixed(unsigned x) {
    switch (x) {
    case 1: return 1;
    case 0x101: return 2;
    case 0xFFFF: return 3;
    case 0x8000: return 4;
    case 0: return 5;
    }
    return 0;
}
int hi_zero_dense(int x) {
    switch (x) {
    case 0: return 10;
    case 1: return 11;
    case 2: return 12;
    case 3: return 13;
    case 4: return 14;
    default: return -1;
    }
}
int hi_full_table(unsigned x) {
    switch (x & 255) {   /* a binary expression stays on the 16-bit path */
    case 1: return 1;
    case 255: return 2;
    }
    return 0;
}
char gc = 5;
unsigned char guc = 255;
int expr_char(void) {
    switch ((char)(gc + 123)) {  /* wraps to -128 */
    case -128: return 1;
    case 128: return 2;
    }
    return 0;
}
int promote(unsigned char a, unsigned char b) {
    switch (a + b) {       /* an int: 300 does not wrap to 44 */
    case 44: return 1;
    case 300: return 2;
    }
    return 0;
}
int main(void) {
    int i;
    for (i = -128; i < 256; i++) {
        printf("%d %d %d %d %d\n", sc_sw((signed char)i),
               uc_sw((unsigned char)i), uc_dense((unsigned char)i),
               sc_dense((signed char)i), sc_wide((signed char)i));
    }
    {
        static int vals[] = {0x1201, 0x1207, 0x12FF, 0x1200, 0x1202, 0x0201,
                             0x1301, 1, 0, -1, -2, -256, 0x300, 0x301, 0x302,
                             0x303, 0x304, 0x305, 0x306, 0x101, 0x8000, 2, 3,
                             4, 5, -255, 0x7FFF};
        for (i = 0; i < 27; i++) {
            int v = vals[i];
            printf("%d %d %d %d %d %d %d\n", hi_same(v), hi_dense(v),
                   hi_neg(v), hi_mixed(v), hi_zero_dense(v),
                   hi_full_table(v), 0);
        }
    }
    printf("%d %d %d %d\n", expr_char(), promote(200, 100), promote(20, 24),
           promote(1, 1));
    return 0;
}
'''


def model_sc_sw(c):
    return {-128: 1, -1: 2, 0: 3, 5: 4, 127: 5}.get(c, -1)


def model_uc_sw(c):
    return {0: 1, 1: 2, 2: 3, 3: 3, 4: 4, 6: 5, 255: 6}.get(c, -1)


def model_uc_dense(c):
    return {250: 1, 251: 2, 252: 3, 254: 4, 255: 5}.get(c, 0)


def model_sc_dense(c):
    return {-3: 1, -2: 2, -1: 3, 0: 4, 2: 5}.get(c, 0)


def model_sc_wide(c):
    r = 0
    if c == -128:
        r += 1
        r += 2
    elif c == -127:
        r += 2
    elif c == 126:
        r += 4
        r += 8
    elif c == 127:
        r += 8
    elif c in (-64, -63, -62):
        r = 100
    return r


@needs_toolchain
def test_switch_values_match_the_model(tmp_path):
    out = build_and_run(tmp_path, SWITCH_SRC)
    exp = []
    for i in range(-128, 256):
        sc = to_c(i, True, 8)
        uc = i & 255
        exp.append(f"{model_sc_sw(sc)} {model_uc_sw(uc)} "
                   f"{model_uc_dense(uc)} {model_sc_dense(sc)} "
                   f"{model_sc_wide(sc)}")
    vals = [0x1201, 0x1207, 0x12FF, 0x1200, 0x1202, 0x0201, 0x1301, 1, 0, -1,
            -2, -256, 0x300, 0x301, 0x302, 0x303, 0x304, 0x305, 0x306, 0x101,
            0x8000, 2, 3, 4, 5, -255, 0x7FFF]
    for v in vals:
        v = to_c(v, True, 16)
        u = v & 0xFFFF
        hs = {0x1201: 1, 0x1207: 2, 0x12FF: 3, 0x1200: 4}.get(u, -1)
        hd = {0x300: 1, 0x301: 2, 0x302: 3, 0x303: 4, 0x305: 5}.get(u, 0)
        hn = {0xFFFF: 1, 0xFFFE: 2, 0xFF00: 3}.get(u, 0)
        hm = {1: 1, 0x101: 2, 0xFFFF: 3, 0x8000: 4, 0: 5}.get(u, 0)
        hz = {0: 10, 1: 11, 2: 12, 3: 13, 4: 14}.get(u, -1)
        hf = {1: 1, 255: 2}.get(u & 255, 0)
        exp.append(f"{hs} {hd} {hn} {hm} {hz} {hf} 0")
    exp.append("1 2 1 0")
    assert out == exp
