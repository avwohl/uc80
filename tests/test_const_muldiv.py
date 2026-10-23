"""Multiply, divide, modulo and shift by a compile-time constant, inline.

The code generator used to call __mul16, __div16/__sdiv16, __mod16/
__smod16, __shl16/__shr16/__sar16 (and the 32-bit helpers, with the
constant staged in __tmp32) for most constant operands, and _gen_address
called __mul16 for every element size that is not 1, 2, 4 or 8.  Now:

  * x * K and K * x: a power of two is add HL,HL (a byte move from 256
    up); another K is a shift-and-add chain on a copy in DE, binary or
    non-adjacent form, whichever is faster; K with 8 or more low zero bits
    works on L alone in A.  Array index and pointer scaling use the same
    code.  Only a chain longer than 24 bytes still calls __mul16.
  * unsigned x / 2**n and x % 2**n: a shift and an AND; signed x / 2**n:
    the round-toward-zero bias, then an arithmetic shift; signed
    x % 2**n: a short branch on the sign.
  * x << K, x >> K for any K in 0..15: inline (byte moves for 8 and up);
    a char operand shifts right in L only.
  * 32 bits: shifts by a constant are byte and word moves plus at most a
    few bit steps (or the helper with A = the rest, never __tmp32);
    x * K with at most 4 binary or NAF digits is shift-and-add through
    __tmp32; unsigned / and % by 2**n are shifts and AND masks; signed /
    by 2**n adds the bias first.

The unit tests below call generate() directly (no AST optimizer and no
peephole) and pin the shape of each fast path.  The end-to-end tests build
whole programs, run them under cpmemu and compare every value with a
Python model of 16-bit-int C.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from uc_core.frontend import parse as _frontend_parse
from uc80.codegen import generate

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"


def gen(source: str) -> str:
    return generate(_frontend_parse(source, "<test>"), enable_inlining=False,
                    enable_const_propagation=False)


def body(code: str, name: str) -> str:
    """The instructions of function ``name`` (up to its return label)."""
    start = code.index(f"\n_{name}:")
    end = code.index(f"@{name}_ret:", start)
    return code[start:end]


def ops(text: str) -> list[str]:
    """Instruction lines, tab-separated, without comments or labels."""
    out = []
    for line in text.splitlines():
        if line.startswith("\t") and not line.startswith("\t;"):
            out.append(line.strip().split(";")[0].strip())
    # Drop the frame set-up (push IX / ld IX,0 / add IX,SP), if any.
    prologue = ["push\tIX", "ld\tIX,0", "add\tIX,SP"]
    if out[:3] == prologue:
        out = out[3:]
    # Drop the jump to the return label at the end of the body.
    while out and out[-1].startswith("jp\t@") and out[-1].endswith("_ret"):
        out.pop()
    return out


GLOBALS = ("int x; unsigned u; long l; unsigned long ul; "
           "unsigned char uc; signed char sc; ")


def fn(expr: str, rtype: str = "int") -> list[str]:
    code = gen(GLOBALS + f"{rtype} f(void) {{ return {expr}; }} "
               "int main(void) { return (int)f(); }")
    return ops(body(code, "f"))


HELPERS = ("__mul16", "__div16", "__sdiv16", "__mod16", "__smod16",
           "__shl16", "__shr16", "__sar16", "__mul32", "__smul32",
           "__div32", "__sdiv32", "__mod32", "__shl32", "__shr32",
           "__sar32", "__and32")


def no_helper(lines: list[str]) -> bool:
    return not any(h in l for l in lines for h in HELPERS)


class TestMul16:
    def test_mul_82_is_shift_add(self):
        o = fn("x * 82")
        assert no_helper(o)
        assert o[1:3] == ["ld\tD,H", "ld\tE,L"]
        assert o.count("add\tHL,DE") == 2
        assert o.count("add\tHL,HL") == 6
        assert "push\tHL" not in o

    def test_const_on_left(self):
        o = fn("3 * x")
        assert no_helper(o)
        assert o == ["ld\tHL,(_x)", "ld\tD,H", "ld\tE,L", "add\tHL,HL",
                     "add\tHL,DE"]

    def test_mul_31_uses_subtract(self):
        # NAF: 32 - 1 is faster than 16+8+4+2+1.
        o = fn("x * 31")
        assert no_helper(o)
        assert "sbc\tHL,DE" in o and o.count("add\tHL,HL") == 5

    def test_mul_256_is_byte_move(self):
        assert fn("x * 256") == ["ld\tHL,(_x)", "ld\tH,L", "ld\tL,0"]

    def test_mul_1024_in_a(self):
        assert fn("x * 1024") == ["ld\tHL,(_x)", "ld\tA,L", "add\tA,A",
                                  "add\tA,A", "ld\tH,A", "ld\tL,0"]

    def test_mul_768_8bit_chain(self):
        o = fn("x * 768")
        assert no_helper(o)
        assert o[-2:] == ["ld\tH,A", "ld\tL,0"] and "add\tA,E" in o

    def test_mul_minus_one_is_negate(self):
        o = fn("x * -1")
        assert no_helper(o) and "sub\tL" in o and "sub\tH" in o

    def test_mul_zero_still_evaluates(self):
        o = gen(GLOBALS + "int g(void); int f(void) { return g() * 0; } "
                "int main(void) { return f(); }")
        f = ops(body(o, "f"))
        assert "call\t_g" in f and f[-1] == "ld\tHL,0"

    def test_mul_by_variable_still_calls(self):
        assert any("__mul16" in l for l in fn("x * (int)u"))


class TestIndexScaling:
    def test_row_of_1024_bytes(self):
        code = gen("int p[13][512]; int f(int i, int k) { return p[i][k]; } "
                   "int main(void) { return f(1, 2); }")
        f = ops(body(code, "f"))
        assert no_helper(f)
        assert "add\tA,A" in f and "ld\tL,0" in f

    def test_struct_of_6_bytes(self):
        code = gen("struct s { int a, b, c; } t[10]; "
                   "int f(int i) { return t[i].b; } "
                   "int main(void) { return f(1); }")
        f = ops(body(code, "f"))
        assert no_helper(f) and "add\tHL,DE" in f

    def test_pointer_plus_int_on_left(self):
        code = gen("struct s { char c[5]; }; "
                   "struct s *f(struct s *p, int i) { return i + p; } "
                   "int main(void) { return f(0, 1) != 0; }")
        f = ops(body(code, "f"))
        assert no_helper(f) and "ex\t(SP),HL" in f

    def test_pointer_difference_exact_division(self):
        code = gen("struct s { char c[6]; }; "
                   "int f(struct s *p, struct s *q) { return p - q; } "
                   "int main(void) { return f(0, 0); }")
        f = ops(body(code, "f"))
        assert "call\t__sdiv16" not in f and "sra\tH" in f


class TestDivMod16:
    def test_unsigned_div_16(self):
        o = fn("u / 16", "unsigned")
        assert no_helper(o)
        assert o.count("srl\tH") == 4 and "push\tHL" not in o

    def test_unsigned_mod_16(self):
        assert fn("u % 16", "unsigned") == [
            "ld\tHL,(_u)", "ld\tA,L", "and\t15", "ld\tL,A", "ld\tH,0"]

    def test_signed_div_8_rounds_toward_zero(self):
        o = fn("x / 8")
        assert no_helper(o)
        assert o[1] == "bit\t7,H" and o[2].startswith("jr\tZ,@SDP")
        assert "ld\tDE,7" in o and o.count("sra\tH") == 3

    def test_signed_div_2_uses_inc(self):
        o = fn("x / 2")
        assert "inc\tHL" in o and no_helper(o)

    def test_signed_mod_8_branches(self):
        o = fn("x % 8")
        assert no_helper(o) and "or\t248" in o and "and\t7" in o

    def test_signed_mod_1024(self):
        o = fn("x % 1024")
        assert no_helper(o) and "and\t3" in o and "or\t252" in o

    def test_uchar_div_is_shift(self):
        # An unsigned char promotes to a non-negative int: no bias.
        o = fn("uc / 4")
        assert no_helper(o) and "bit\t7,H" not in o

    def test_div_by_3_still_calls(self):
        assert any("__sdiv16" in l for l in fn("x / 3"))

    def test_signed_div_by_negative_pow2_still_calls(self):
        assert any("__sdiv16" in l for l in fn("x / -4"))


class TestShift16:
    def test_shr_8_unsigned(self):
        assert fn("u >> 8", "unsigned") == ["ld\tHL,(_u)", "ld\tL,H",
                                            "ld\tH,0"]

    def test_shr_8_signed(self):
        assert fn("x >> 8") == ["ld\tHL,(_x)", "ld\tL,H", "ld\tA,H",
                                "rlca", "sbc\tA,A", "ld\tH,A"]

    def test_shr_12_is_byte_move_and_4(self):
        o = fn("u >> 12", "unsigned")
        assert o[1:3] == ["ld\tL,H", "ld\tH,0"]
        assert o.count("srl\tL") == 4 and no_helper(o)

    def test_shr_6_via_left_shift(self):
        o = fn("u >> 6", "unsigned")
        assert no_helper(o)
        assert o.count("add\tHL,HL") == 2 and o[-2:] == ["ld\tL,H",
                                                         "ld\tH,A"]

    def test_shl_8_and_10(self):
        assert fn("x << 8") == ["ld\tHL,(_x)", "ld\tH,L", "ld\tL,0"]
        o = fn("x << 10")
        assert o[1:] == ["ld\tA,L", "add\tA,A", "add\tA,A", "ld\tH,A",
                         "ld\tL,0"]

    def test_shl_7(self):
        o = fn("x << 7")
        assert no_helper(o) and "rra" in o and o.count("add\tHL,HL") == 0

    def test_char_shift_in_l_only(self):
        o = fn("uc >> 3")
        assert o.count("srl\tL") == 3 and "rr\tL" not in o
        o = fn("sc >> 2")
        assert o.count("sra\tL") == 2 and "rr\tL" not in o

    def test_compound_shift(self):
        code = gen(GLOBALS + "void f(void) { u >>= 9; x <<= 9; } "
                   "int main(void) { f(); return 0; }")
        assert no_helper(ops(body(code, "f")))

    def test_out_of_range_count_keeps_helper(self):
        assert any("__shl16" in l for l in fn("x << 16"))


class TestOps32:
    def test_shr_16_is_word_move(self):
        o = fn("ul >> 16", "unsigned long")
        assert no_helper(o) and "__tmp32" not in " ".join(o)
        assert "ex\tDE,HL" in o and "ld\tDE,0" in o

    def test_shl_8_is_byte_move(self):
        o = fn("l << 8", "long")
        assert o[2:] == ["ld\tD,E", "ld\tE,H", "ld\tH,L", "ld\tL,0"]

    def test_shr_12_moves_then_helper_with_a(self):
        o = fn("ul >> 12", "unsigned long")
        assert "ld\tA,4" in o and "call\t__shr32" in o
        assert "ld\t(__tmp32),HL" not in o

    def test_sar_24(self):
        o = fn("l >> 24", "long")
        assert no_helper(o) and o[2] == "ld\tL,D"

    def test_mul_5(self):
        o = fn("ul * 5", "unsigned long")
        assert no_helper(o) and "adc\tHL,BC" in o

    def test_mod_16_is_and(self):
        o = fn("ul % 16", "unsigned long")
        assert no_helper(o) and "and\t15" in o and "ld\tDE,0" in o

    def test_signed_div_4(self):
        o = fn("l / 4", "long")
        assert no_helper(o) and "bit\t7,D" in o

    def test_signed_mod_keeps_helper(self):
        assert any("__smod32" in l for l in fn("l % 4", "long"))

    def test_mul_big_constant_keeps_helper(self):
        assert any("__mul32" in l
                   for l in fn("ul * 1103515245UL", "unsigned long"))


class TestConstantWraps:
    """Each operator inside the constant wraps at its own C type, as the
    general code computes it: 65535u + 1u is 0, and so is 256u * 256u.
    generate() runs without the AST optimizer, so nothing is folded."""

    def test_unsigned_sum_wraps_before_widening(self):
        o = fn("l * (long)(65535u + 1u)", "long")
        assert no_helper(o) and o[-2:] == ["ld\tHL,0", "ld\tDE,0"]

    def test_unsigned_product_wraps_before_bool(self):
        o = fn("x * (_Bool)(256u * 256u)")
        assert o[-1] == "ld\tHL,0"

    def test_unsigned_negation_wraps(self):
        # (long)-1u is 65535, not -1: no negate of the whole DEHL.
        o = fn("l / (long)-1u", "long")
        assert any("__sdiv32" in l for l in o)

    def test_unsigned_sum_shift_count(self):
        # (65535u + 4u) is 3: an inline shift, not the helper.
        o = fn("x << (65535u + 4u)")
        assert no_helper(o) and o.count("add\tHL,HL") == 3


# ------------------------------------------------------- end to end

def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


def build_and_run(tmp_path, source, name="prog"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text("#include <stdio.h>\n" + source)
    mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac)], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    env = dict(os.environ, PYTHONHASHSEED="0")
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=120, env=env)
    return run.stdout.decode("latin-1").replace("\r", "")


def wrap(v, bits, signed):
    v &= (1 << bits) - 1
    if signed and v >> (bits - 1):
        v -= 1 << bits
    return v


def c_div(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def c_op(a, op, k, bits, signed):
    """a op k in C, with a and k already of the operation's type."""
    if op == "*":
        r = a * k
    elif op == "/":
        r = c_div(a, k)
    elif op == "%":
        r = a - c_div(a, k) * k
    elif op == "<<":
        r = a << k
    else:
        r = a >> k                     # Python >> is arithmetic
    return wrap(r, bits, signed)


V16 = [0, 1, -1, 2, -2, 3, 5, -7, 15, 16, -16, 100, -100, 127, -128, 128,
       255, 256, -255, -256, 1000, -1000, 1234, -4321, 4096, 0x5555,
       -0x5555, 0x7F00, 32767, -32767, -32768]
V32 = [0, 1, -1, 7, -7, 255, -256, 65535, 65536, -65536, 123456, -123456,
       0x12345678, -0x12345678, 0x7FFFFFFF, -0x80000000, 0x00FF00FF,
       1103515245, -99999]
V8 = [0, 1, 2, 7, 15, 64, 100, 127, 128, 129, 200, 255]

K16_MUL = [0, 1, 2, 3, 5, 6, 7, 9, 10, 12, 15, 31, 41, 82, 100, 127, 255,
           256, 257, 300, 512, 768, 1000, 1024, 0x5555, 0x7FFF, 0x8000,
           -1, -2, -3, -10, -256]
K16_DIV = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 4096, 16384]
K16_SH = list(range(16))
K32_MUL = [0, 1, 2, 3, 5, 7, 10, 31, 100, 255, 256, 1000, 65536, 65537,
           0x01000001, 1103515245]
K32_DIV = [1, 2, 4, 8, 16, 256, 4096, 65536, 1 << 20, 1 << 24, 1 << 30]
K32_SH = list(range(32))


def _cases():
    """(C type, value bits, signed, op, K, compound) for every test."""
    out = []
    for t, s in (("int", True), ("unsigned", False)):
        out += [(t, 16, s, "*", k) for k in K16_MUL]
        out += [(t, 16, s, op, k) for op in "/%" for k in K16_DIV]
        out += [(t, 16, s, op, k) for op in ("<<", ">>") for k in K16_SH]
    for t, s in (("long", True), ("unsigned long", False)):
        out += [(t, 32, s, "*", k) for k in K32_MUL]
        out += [(t, 32, s, op, k) for op in "/%" for k in K32_DIV]
        out += [(t, 32, s, op, k) for op in ("<<", ">>") for k in K32_SH]
    return out


def _lit(k, t):
    suffix = {"int": "", "unsigned": "u", "long": "L",
              "unsigned long": "UL"}[t]
    if k < 0:
        return f"(-{-k}{suffix})"
    return f"{k}{suffix}"


def _program(cases, compound):
    lines = []
    lines.append("volatile int v16[] = {%s};" % ",".join(map(str, V16)))
    lines.append("volatile long v32[] = {%s};" % ",".join(
        f"{v}L" if v != -0x80000000 else "(-2147483647L-1)" for v in V32))
    funcs, calls = [], []
    for n, (t, bits, s, op, k) in enumerate(cases):
        arr, cnt = ("v16", len(V16)) if bits == 16 else ("v32", len(V32))
        fmt = {"int": "%d", "unsigned": "%u", "long": "%ld",
               "unsigned long": "%lu"}[t]
        lit = _lit(k, t) if op not in ("<<", ">>") else str(k)
        if compound:
            stmt = f"{t} r = ({t}){arr}[i]; r {op}= {lit};"
        else:
            stmt = f"{t} r = ({t}){arr}[i] {op} {lit};"
        funcs.append(
            f"void t{n}(void) {{ int i; for (i = 0; i < {cnt}; i++) "
            f"{{ {stmt} printf(\"{fmt} \", r); }} printf(\"\\n\"); }}")
        calls.append(f"t{n}();")
    lines += funcs
    lines.append("int main(void) { %s return 0; }" % " ".join(calls))
    return "\n".join(lines)


def _expected(cases):
    rows = []
    for t, bits, s, op, k in cases:
        vals = V16 if bits == 16 else V32
        row = []
        for v in vals:
            a = wrap(v, bits, s)
            kk = wrap(k, bits, s) if op not in ("<<", ">>") else k
            row.append(str(c_op(a, op, kk, bits, s)))
        rows.append(" ".join(row))
    return rows


needs_toolchain = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or built libraries not available")


def _check(tmp_path, cases, compound, name):
    out = build_and_run(tmp_path, _program(cases, compound), name)
    got = [l.strip() for l in out.splitlines()
           if l.strip() and not l.startswith(("CPU mode", "Loaded",
                                              "Program exit"))]
    want = _expected(cases)
    assert len(got) == len(want), out[-2000:]
    bad = [(c, w, g) for c, w, g in zip(cases, want, got) if w != g]
    assert not bad, bad[:5]


@needs_toolchain
@pytest.mark.parametrize("bits", [16, 32])
@pytest.mark.parametrize("compound", [False, True])
def test_values(tmp_path, bits, compound):
    cases = [c for c in _cases() if c[1] == bits]
    _check(tmp_path, cases, compound, f"v{bits}{'c' if compound else ''}")


@needs_toolchain
def test_char_operands(tmp_path):
    """Char operands: the H byte is the extension of L."""
    src = ["volatile unsigned char u8[] = {%s};" % ",".join(map(str, V8)),
           "volatile signed char s8[] = {%s};" % ",".join(
               str(wrap(v, 8, True)) for v in V8)]
    body_, want = [], []
    for arr, signed in (("u8", False), ("s8", True)):
        for op, ks in ((">>", range(10)), ("/", (1, 2, 4, 8, 64, 128, 256)),
                       ("%", (1, 2, 8, 128, 256)), ("*", (3, 10, 256))):
            for k in ks:
                body_.append(
                    f"for (i = 0; i < {len(V8)}; i++) printf(\"%d \", "
                    f"{arr}[i] {op} {k}); printf(\"\\n\");")
                want.append(" ".join(
                    str(c_op(wrap(v, 8, signed), op, k, 16, True))
                    for v in V8))
    src.append("int main(void) { int i; %s return 0; }" % " ".join(body_))
    out = build_and_run(tmp_path, "\n".join(src), "ch")
    got = [l.strip() for l in out.splitlines()
           if l.strip() and not l.startswith(("CPU mode", "Loaded",
                                              "Program exit"))]
    assert got == want


@needs_toolchain
def test_index_scaling_and_pointer_difference(tmp_path):
    """Arrays of structs of many sizes: element addresses, values and
    pointer differences (exact division by the size)."""
    sizes = [3, 5, 6, 7, 10, 12, 41, 82, 100, 255, 256, 300, 1000, 1024]
    src, want = [], []
    for s in sizes:
        n = max(2, min(8, 3000 // s))
        src.append(f"struct s{s} {{ unsigned char b[{s}]; }} a{s}[{n}];")
    lines = []
    for s in sizes:
        n = max(2, min(8, 3000 // s))
        lines.append(
            f"for (i = 0; i < {n}; i++) {{ a{s}[i].b[{s} - 1] = i * 7 + 1; }}"
            f"for (i = 0; i < {n}; i++) {{ struct s{s} *p = a{s} + i; "
            f"struct s{s} *q = i + a{s}; printf(\"%d %d %d %d \", "
            f"a{s}[i].b[{s} - 1], (int)(p - a{s}), (int)(&a{s}[i] - q), "
            f"(int)((char *)p - (char *)a{s})); }} printf(\"\\n\");")
        want.append(" ".join(f"{i * 7 + 1} {i} 0 {i * s}" for i in range(n)))
    src.append("int main(void) { int i; %s return 0; }" % " ".join(lines))
    out = build_and_run(tmp_path, "\n".join(src), "ix")
    got = [l.strip() for l in out.splitlines()
           if l.strip() and not l.startswith(("CPU mode", "Loaded",
                                              "Program exit"))]
    assert got == want


@needs_toolchain
def test_constant_operand_forms(tmp_path):
    """The constant can be an enum, a cast (also to a typedef name, which
    must narrow the value) or sizeof.  _eval_const_expr does not narrow a
    cast to uint8_t, so the fast path has its own evaluator."""
    src = r"""
#include <stdint.h>
typedef unsigned char u8;
typedef signed char s8;
enum { E5 = 5, EBIG = 300 };
volatile int v = 7; volatile unsigned uv = 40000u; volatile long lv = 100000L;
int main(void) {
  int x = v; unsigned u = uv; long l = lv;
  printf("%d %d %d %d\n", x * (unsigned char)300, x * (u8)300,
         x * (uint8_t)258, x * (s8)200);
  printf("%d %d %d\n", x * E5, x * (char)EBIG, x << (u8)259);
  printf("%u %u %d\n", u / (u8)260, u % (uint16_t)65544L,
         x * (int)sizeof("abcdef"));
  printf("%ld %ld\n", l * (u8)261, l >> (u8)264);
  printf("%d %d\n", x * (short)65539L, x * (int16_t)65540L);
  return 0;
}
"""
    out = build_and_run(tmp_path, src, "forms")
    got = [l.strip() for l in out.splitlines()
           if l.strip() and not l.startswith(("CPU mode", "Loaded",
                                              "Program exit"))]
    assert got == ["308 308 14 -392", "35 308 56", "10000 0 49",
                   "500000 390", "21 28"]
