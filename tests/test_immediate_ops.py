"""Immediate and simple operands for + - & | ^, and 8-bit evaluation in A.

The code generator used to evaluate every binary operator as ``gen left /
push HL / gen right / pop DE`` followed by a generic 16-bit sequence, and
every char value was widened to HL first.  Now:

  * x + K and x - K: inc/dec HL for 1..3, the high byte alone when K's low
    byte is 0, else ``ld DE,K / add HL,DE``; a pointer scales K at compile
    time.  x & | ^ K works on each byte with an immediate and leaves a byte
    alone when K's byte is FF (&) or 00 (| ^).  Two constant operands fold.
  * A right operand that is a constant or a scalar variable is loaded
    straight into DE (or into HL after ``ex DE,HL``), with no push/pop.
  * A value that is used only as a char (stored to a char object, returned
    as a char, cast to a char) is computed in A when its low byte depends
    only on the low bytes of the operands: + - & | ^ ~ unary -, << by a
    constant, and >> by a constant of a value known to be 0..255 or
    -128..127.  ``reg |= 4`` is ``ld A,(reg) / or 4 / ld (reg),A``.
  * A comparison whose operands are both known to be 0..255, or both
    -128..127 (in a signed comparison), is done on one byte in A.

The unit tests call generate() directly (no AST optimizer and no
peephole) and pin the shape of each fast path.  The end-to-end tests build
whole programs, run them under cpmemu and compare every value with a
Python model of 16-bit-int C.
"""

import os
import re
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
                    enable_const_propagation=False,
                    enable_dead_elimination=False)


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
    prologue = ["push\tIX", "ld\tIX,0", "add\tIX,SP"]
    if out[:3] == prologue:
        out = out[3:]
    while out and out[-1].startswith("jp\t@") and out[-1].endswith("_ret"):
        out.pop()
    return out


GLOBALS = ("int x; unsigned u; unsigned char uc; signed char sc; "
           "int *ip; char *cp; unsigned char buf[8]; unsigned char *p; "
           "struct S { unsigned char a; int b; } s, *ps; ")


def fn(stmts: str, rtype: str = "int", params: str = "void") -> list[str]:
    code = gen(GLOBALS + f"{rtype} f({params}) {{ {stmts} }} "
               "int main(void) { return 0; }")
    return ops(body(code, "f"))


def ret(expr: str, rtype: str = "int", params: str = "void") -> list[str]:
    return fn(f"return {expr};", rtype, params)


class TestImmediate16:
    def test_add_1_is_inc(self):
        assert ret("x + 1") == ["ld\tHL,(_x)", "inc\tHL"]

    def test_sub_2_is_dec(self):
        assert ret("x - 2") == ["ld\tHL,(_x)", "dec\tHL", "dec\tHL"]

    def test_const_on_left_of_add(self):
        assert ret("3 + x") == ["ld\tHL,(_x)", "inc\tHL", "inc\tHL",
                                "inc\tHL"]

    def test_add_high_byte_only(self):
        assert ret("x + 0x300") == ["ld\tHL,(_x)", "ld\tA,H", "add\tA,3",
                                    "ld\tH,A"]

    def test_add_large(self):
        assert ret("x + 1000") == ["ld\tHL,(_x)", "ld\tDE,1000",
                                   "add\tHL,DE"]

    def test_and_low_byte(self):
        assert ret("x & 0x80") == ["ld\tHL,(_x)", "ld\tA,L", "and\t128",
                                   "ld\tL,A", "ld\tH,0"]

    def test_or_high_byte_only(self):
        assert ret("x | 0x1200") == ["ld\tHL,(_x)", "ld\tA,H", "or\t18",
                                     "ld\tH,A"]

    def test_xor_ff_is_cpl(self):
        assert ret("x ^ 0xFF00") == ["ld\tHL,(_x)", "ld\tA,H", "cpl",
                                     "ld\tH,A"]

    def test_and_ffff_is_nothing(self):
        assert ret("x & 0xFFFF") == ["ld\tHL,(_x)"]

    def test_pointer_scales_constant(self):
        assert ret("ip + 3", "int *") == ["ld\tHL,(_ip)", "ld\tDE,6",
                                          "add\tHL,DE"]
        assert ret("ip - 1", "int *") == ["ld\tHL,(_ip)", "dec\tHL",
                                          "dec\tHL"]
        assert ret("cp + 1", "char *") == ["ld\tHL,(_cp)", "inc\tHL"]

    def test_unsigned_char_and_in_a(self):
        # The operand is 0..255, so H stays 0.
        assert ret("uc & 0x0F") == ["ld\tA,(_uc)", "and\t15", "ld\tL,A",
                                    "ld\tH,0"]

    def test_constants_fold(self):
        assert ret("0xF0 | 0x0F") == ["ld\tHL,255"]
        assert ret("5 - 3") == ["ld\tHL,2"]

    def test_k_minus_x_has_no_push(self):
        o = ret("5 - x")
        assert "push\tHL" not in o
        assert o[-2:] == ["or\tA", "sbc\tHL,DE"]


class TestSimpleOperand:
    def test_params_without_push(self):
        assert ret("a + b", params="int a, int b") == [
            "ld\tL,(IX+4)", "ld\tH,(IX+5)", "ld\tE,(IX+6)",
            "ld\tD,(IX+7)", "add\tHL,DE"]

    def test_subtract_global(self):
        assert ret("x - u") == ["ld\tHL,(_x)", "ld\tDE,(_u)", "or\tA",
                                "sbc\tHL,DE"]

    def test_char_operand_extends_into_de(self):
        assert ret("x + sc") == ["ld\tHL,(_x)", "ld\tA,(_sc)", "ld\tE,A",
                                 "ld\tA,E", "rlca", "sbc\tA,A", "ld\tD,A",
                                 "add\tHL,DE"]

    def test_compare_without_push(self):
        o = ret("a < x", params="int a")
        assert "push\tHL" not in o
        assert o[:5] == ["ld\tL,(IX+4)", "ld\tH,(IX+5)", "ld\tDE,(_x)",
                         "or\tA", "sbc\tHL,DE"]

    def test_divide_by_variable_keeps_contract(self):
        # DE = left, HL = right for the helper.
        o = ret("x / u")
        assert o == ["ld\tHL,(_x)", "ex\tDE,HL", "ld\tHL,(_u)",
                     "call\t__div16"]

    def test_simple_left_complex_right(self):
        # Right first (no side effects), then left straight into DE.
        o = ret("x - (u & x)")
        assert "push\tHL" not in o
        assert "ld\tDE,(_x)" in o


class TestCharInA:
    def test_or_assign(self):
        assert fn("uc |= 0x04;", "void") == ["ld\tA,(_uc)", "or\t4",
                                             "ld\t(_uc),A"]

    def test_and_assign_complement(self):
        assert fn("uc &= ~0x80;", "void") == ["ld\tA,(_uc)", "and\t127",
                                              "ld\t(_uc),A"]

    def test_add_assign_one_is_inc(self):
        assert fn("sc += 1;", "void") == ["ld\tA,(_sc)", "inc\tA",
                                          "ld\t(_sc),A"]

    def test_value_used_is_reextended(self):
        o = ret("sc = sc - 1")
        assert o == ["ld\tA,(_sc)", "dec\tA", "ld\t(_sc),A", "ld\tL,A",
                     "ld\tA,L", "rlca", "sbc\tA,A", "ld\tH,A"]

    def test_constant_store_through_pointer(self):
        assert fn("*p = 0;", "void") == ["ld\tHL,(_p)", "ld\t(HL),0"]

    def test_store_through_pointer_keeps_a(self):
        assert fn("ps->a = uc + 1;", "void") == [
            "ld\tA,(_uc)", "inc\tA", "ld\tHL,(_ps)", "ld\t(HL),A"]

    def test_element_xor_assign_address_once(self):
        o = fn("buf[i] ^= 1;", "void", "int i")
        assert o.count("ld\tHL,_buf") == 1
        assert o[-3:] == ["ld\tA,(HL)", "xor\t1", "ld\t(HL),A"]

    def test_char_return(self):
        assert ret("c + 3", "unsigned char", "unsigned char c") == [
            "ld\tA,(IX+4)", "add\tA,3", "ld\tL,A", "ld\tH,0"]

    def test_char_return_variable_operand(self):
        assert ret("a - b", "signed char", "signed char a, signed char b") == [
            "ld\tA,(IX+4)", "sub\t(IX+6)", "ld\tL,A", "ld\tA,L", "rlca",
            "sbc\tA,A", "ld\tH,A"]

    def test_shifts(self):
        assert fn("uc = uc >> 4;", "void") == [
            "ld\tA,(_uc)", "rrca", "rrca", "rrca", "rrca", "and\t15",
            "ld\t(_uc),A"]
        assert fn("sc = sc >> 1;", "void") == ["ld\tA,(_sc)", "sra\tA",
                                               "ld\t(_sc),A"]
        assert fn("uc = x << 3;", "void") == [
            "ld\tA,(_x)", "add\tA,A", "add\tA,A", "add\tA,A",
            "ld\t(_uc),A"]

    def test_int_shift_right_is_not_narrowed(self):
        # x >> 4 needs the high byte of x.
        o = fn("uc = x >> 4;", "void")
        assert "ld\tA,(_x)" not in o

    def test_cast_to_char(self):
        assert ret("(unsigned char)(x + 1)") == [
            "ld\tA,(_x)", "inc\tA", "ld\tL,A", "ld\tH,0"]

    def test_float_is_not_narrowed(self):
        code = gen("float f; unsigned char c; "
                   "void g(void) { c = f + 1; } int main(void) { return 0; }")
        assert "call\t__ftoi" in code or "call\t__ftou" in code


class TestByteCompare:
    def test_equal_constant(self):
        assert ret("uc == 'x'") == ["ld\tA,(_uc)", "sub\t120", "sub\t1",
                                    "sbc\tA,A", "and\t1", "ld\tL,A",
                                    "ld\tH,0"]

    def test_unsigned_greater(self):
        assert ret("uc > 200") == ["ld\tA,(_uc)", "cp\t201", "ccf",
                                   "sbc\tA,A", "and\t1", "ld\tL,A",
                                   "ld\tH,0"]

    def test_constant_on_left(self):
        assert ret("10 < uc")[:3] == ["ld\tA,(_uc)", "cp\t11", "ccf"]

    def test_signed_flips_sign(self):
        assert ret("sc < 5")[:3] == ["ld\tA,(_sc)", "xor\t128", "cp\t133"]

    def test_out_of_range_constant_keeps_16_bit(self):
        assert "sbc\tHL,DE" in ret("uc < 300")
        assert "sbc\tHL,DE" in ret("sc > -129")

    def test_negative_char_against_unsigned_keeps_16_bit(self):
        # (signed char)x >= 16u is an unsigned comparison: -12 is 65524.
        assert "sbc\tHL,DE" in ret("(signed char)x >= 16u")
        assert "sbc\tHL,DE" in ret("sc <= 7u")

    def test_mixed_signedness_keeps_16_bit(self):
        assert "sbc\tHL,DE" in ret("uc <= sc")

    def test_two_chars(self):
        o = ret("uc == c", params="unsigned char c")
        assert o[:2] == ["ld\tA,(_uc)", "sub\t(IX+4)"]

    def test_masked_value(self):
        assert ret("(x & 0x80) == 0")[:2] == ["ld\tA,(_x)", "and\t128"]


# ---------------------------------------------------------------- e2e

def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


needs_toolchain = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or built libraries not available")


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
    out = run.stdout.decode("latin-1").replace("\r", "")
    return [l.strip() for l in out.splitlines()
            if l.strip() and not l.startswith(("CPU mode", "Loaded",
                                               "Program exit"))]


def _run_lines(tmp_path, decls, lines, want, name, chunk=120):
    """Run the statements ``lines`` (each prints one line) in programs of
    at most ``chunk`` statements, so each fits in the CP/M TPA, and
    compare the output lines with ``want``."""
    assert len(lines) == len(want)
    for start in range(0, len(lines), chunk):
        part = lines[start:start + chunk]
        src = decls + ("\nint main(void) { int i; %s return 0; }"
                       % "\n".join(part))
        got = build_and_run(tmp_path, src, f"{name}{start // chunk}")
        exp = want[start:start + chunk]
        assert len(got) == len(exp), (start, got[-3:])
        bad = [(start + i, part[i], w, g)
               for i, (w, g) in enumerate(zip(exp, got)) if w != g]
        assert not bad, bad[:5]


def wrap(v, bits, signed):
    v &= (1 << bits) - 1
    if signed and v >> (bits - 1):
        v -= 1 << bits
    return v


# Operand arrays: (C element type, values, bits, signed).
ARRS = {
    "u8": ("unsigned char", [0, 1, 2, 7, 15, 16, 100, 127, 128, 129, 200,
                             254, 255], 8, False),
    "s8": ("signed char", [0, 1, 2, 7, 15, 16, 100, 127, -128, -127, -56,
                           -2, -1], 8, True),
    "i16": ("int", [0, 1, -1, 2, 127, 128, 255, 256, -129, 0x1234,
                    -0x1234, 32767, -32768], 16, True),
    "u16": ("unsigned", [0, 1, 65535, 2, 127, 128, 255, 256, 0x8000,
                         0x1234, 0xFEDC, 32767, 0x80FF], 16, False),
}
N = 13


def _decls():
    out = []
    for name, (ct, vals, bits, s) in ARRS.items():
        out.append(f"volatile {ct} {name}[{N}] = {{{','.join(map(str, vals))}}};")
    return "\n".join(out)


def _promote(v, bits, signed):
    """(value, unsigned?) of an operand after integer promotion."""
    if bits == 8:
        return v, False
    return v, not signed


def _c_binop(a, au, op, b, bu):
    """a op b in 16-bit C with promoted operands; (value, unsigned)."""
    uns = au or bu
    if op in ("<<", ">>"):
        uns = au
        if op == "<<":
            r = a << b
        else:
            r = (a & 0xFFFF) >> b if au else a >> b
    else:
        r = {"+": a + b, "-": a - b, "&": a & b, "|": a | b,
             "^": a ^ b}[op]
    return wrap(r, 16, not uns), uns


def _c_cmp(a, au, op, b, bu):
    if au or bu:
        a, b = a & 0xFFFF, b & 0xFFFF
    return int({"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b,
                "==": a == b, "!=": a != b}[op])


KS = [0, 1, 2, 3, 4, 15, 127, 128, 255, 256, 0x300, 0xFF00, 1000, -1, -2,
      -3, -128, -129, -256, 0x7FFF]


def _lit(k):
    """A C literal of int type (unsigned int, in hex, above 32767)."""
    if k < 0:
        return f"({k})"
    return hex(k) if k > 32767 else str(k)


@needs_toolchain
def test_immediate_16bit_values(tmp_path):
    """x op K and K op x as int values, for every operand type."""
    lines, want = [], []
    for name, (ct, vals, bits, s) in ARRS.items():
        for op in ("+", "-", "&", "|", "^"):
            for k in KS:
                for k_left in (False, True):
                    if k_left:
                        expr = f"{_lit(k)} {op} {name}[i]"
                    else:
                        expr = f"{name}[i] {op} {_lit(k)}"
                    lines.append(
                        f"for (i = 0; i < {N}; i++) printf(\"%d \", "
                        f"(int)({expr})); printf(\"\\n\");")
                    row = []
                    for v in vals:
                        a, au = _promote(v, bits, s)
                        kk = wrap(k, 16, True)
                        if k_left:
                            r, _ = _c_binop(kk, False, op, a, au)
                        else:
                            r, _ = _c_binop(a, au, op, kk, False)
                        row.append(str(wrap(r, 16, True)))
                    want.append(" ".join(row))
    _run_lines(tmp_path, _decls(), lines, want, "imm16")


@needs_toolchain
def test_pointer_plus_constant(tmp_path):
    src = r"""
int a[10] = {0,1,2,3,4,5,6,7,8,9};
long l[4] = {10L, 20L, 30L, 40L};
char c[5] = "abcd";
int main(void) {
  int *p = a + 2; long *q = l; char *r = c;
  printf("%d %d %d %d\n", *(p + 3), *(p - 2), *(3 + p), (int)((p + 1) - a));
  printf("%ld %ld %c %c\n", *(q + 3), *(1 + q), *(r + 1), *(r + 3));
  printf("%d\n", (int)(&a[7] - 2 - a));
  return 0;
}
"""
    assert build_and_run(tmp_path, src, "ptr") == [
        "5 0 5 3", "40 20 b d", "5"]


# Expression forms computed for a char target.  x, y: operand arrays.
FORMS = [
    "x + 3", "x - 1", "x + 200", "x & 0x7F", "x | 0x81", "x ^ 0xFF",
    "x ^ 0x5A", "x + y", "x - y", "y - x", "x & y", "x | y", "x ^ y",
    "~x", "-x", "100 - x", "x << 1", "x << 3", "x << 8", "x << 9",
    "(x + 1) & 0x7F", "(x ^ y) + 7", "x + y * 3", "(x & 0xF0) >> 4",
    "x - (y & 3)", "x + (x | y)",
]
FORMS_SHR = ["x >> 1", "x >> 3", "x >> 7", "x >> 8", "x >> 9"]


def _eval_form(form, xv, xt, yv, yt):
    """Value of a form (16-bit C) with x, y of the given (bits, signed)."""
    x, xu = _promote(xv, *xt)
    y, yu = _promote(yv, *yt)
    env = {"x": (x, xu), "y": (y, yu)}

    # A tiny evaluator for the forms above (C precedence).
    import ast as pyast
    tree = pyast.parse(form.replace("~", "~"), mode="eval").body

    def ev(n):
        if isinstance(n, pyast.Name):
            return env[n.id]
        if isinstance(n, pyast.Constant):
            return n.value, False
        if isinstance(n, pyast.UnaryOp):
            v, u = ev(n.operand)
            r = ~v if isinstance(n.op, pyast.Invert) else -v
            return wrap(r, 16, not u), u
        if isinstance(n, pyast.BinOp):
            a, au = ev(n.left)
            b, bu = ev(n.right)
            if isinstance(n.op, pyast.Mult):
                return wrap(a * b, 16, not (au or bu)), au or bu
            op = {pyast.Add: "+", pyast.Sub: "-", pyast.BitAnd: "&",
                  pyast.BitOr: "|", pyast.BitXor: "^", pyast.LShift: "<<",
                  pyast.RShift: ">>"}[type(n.op)]
            return _c_binop(a, au, op, b, bu)
        raise ValueError(form)
    return ev(tree)[0]


def _assign_program(target_kind):
    """Each form assigned to unsigned char and signed char targets of one
    kind (global, local, element, member, *p, compound assignment)."""
    lines, want = [], []
    pairs = [("u8", "s8"), ("s8", "u8"), ("i16", "u8"), ("u16", "s8"),
             ("u8", "i16")]
    for xa, ya in pairs:
        _, xvals, xb, xs = ARRS[xa]
        _, yvals, yb, ys = ARRS[ya]
        forms = FORMS + (FORMS_SHR if xb == 8 else [])
        for form in forms:
            cexpr = re.sub(r"\by\b", f"{ya}[i]",
                           re.sub(r"\bx\b", f"{xa}[i]", form))
            for tt, tsigned in (("uc", False), ("sc", True)):
                if target_kind == "global":
                    stmt = f"g{tt} = {cexpr}; r = g{tt};"
                elif target_kind == "local":
                    stmt = f"l{tt} = {cexpr}; r = l{tt};"
                elif target_kind == "element":
                    stmt = f"a{tt}[i & 3] = {cexpr}; r = a{tt}[i & 3];"
                elif target_kind == "member":
                    stmt = f"ps->{tt} = {cexpr}; r = st.{tt};"
                elif target_kind == "deref":
                    stmt = f"*p{tt} = {cexpr}; r = *p{tt};"
                elif target_kind == "value":
                    stmt = f"r = (g{tt} = {cexpr});"
                else:
                    raise ValueError(target_kind)
                lines.append(f"for (i = 0; i < {N}; i++) {{ {stmt} "
                             f"printf(\"%d \", r); }} printf(\"\\n\");")
                want.append(" ".join(
                    str(wrap(_eval_form(form, xv, (xb, xs), yv, (yb, ys)),
                             8, tsigned))
                    for xv, yv in zip(xvals, yvals)))
    src = (_decls() + "\nunsigned char guc; signed char gsc;"
           "\nunsigned char auc[4]; signed char asc[4];"
           "\nstruct T { int pad; unsigned char uc; signed char sc; } st, "
           "*ps = &st;"
           "\nunsigned char *puc = &auc[1]; signed char *psc = &asc[2];"
           "\nint main(void) { int i; int r; unsigned char luc; "
           "signed char lsc;\n%s\nreturn 0; }" % "\n".join(lines))
    return src, want


@needs_toolchain
@pytest.mark.parametrize("target_kind", ["global", "local", "element",
                                         "member", "deref", "value"])
def test_char_assignment_values(tmp_path, target_kind):
    src, want = _assign_program(target_kind)
    got = build_and_run(tmp_path, src, "as_" + target_kind)
    assert len(got) == len(want)
    bad = [(i, w, g) for i, (w, g) in enumerate(zip(want, got)) if w != g]
    assert not bad, bad[:5]


@needs_toolchain
def test_char_compound_and_return(tmp_path):
    """op= on char variables, elements (address computed once, also with
    a side effect) and members; char return values; casts to char."""
    src = r"""
volatile unsigned char src8[6] = {0, 1, 127, 128, 200, 255};
unsigned char g; signed char gs; unsigned char arr[6]; signed char sarr[6];
struct M { char x; unsigned char m; } mm, *pm = &mm;
unsigned char ret_u(unsigned char a, int k) { return a * 2 + k; }
signed char ret_s(signed char a, signed char b) { return a - b; }
signed char ret_shift(signed char a) { return a >> 2; }
int main(void) {
  int i, j;
  for (i = 0; i < 6; i++) {
    g = src8[i]; g |= 0x41; g ^= 3; g += 100; g -= src8[5 - i];
    gs = src8[i]; gs &= 0xF7; gs += 3; gs = -gs;
    arr[i] = src8[i]; arr[i] += i; arr[i] ^= 0x80;
    sarr[i] = src8[i]; sarr[i] -= 1; sarr[i] |= src8[5 - i] & 3;
    pm->m = src8[i]; pm->m += 7; pm->m <<= 1;
    printf("%d %d %d %d %d ", g, gs, arr[i], sarr[i], mm.m);
    printf("%d %d %d ", ret_u(src8[i], i), ret_s(src8[i], 3),
           ret_shift(src8[i]));
    printf("%d %d\n", (unsigned char)(src8[i] + 77),
           (signed char)(src8[i] ^ 0x5A));
  }
  j = 0;
  for (i = 0; i < 6; i++) arr[j++] += 1;
  for (i = 0; i < 6; i++) sarr[j++ - 6] ^= 0x0F;
  for (i = 0; i < 6; i++) printf("%d %d ", arr[i], sarr[i]);
  printf("%d\n", j);
  return 0;
}
"""
    def s8(v):
        return wrap(v, 8, True)

    def u8(v):
        return v & 0xFF

    vals = [0, 1, 127, 128, 200, 255]
    want, arr, sarr = [], [], []
    for i, v in enumerate(vals):
        g = u8(v | 0x41)
        g = u8(g ^ 3)
        g = u8(g + 100)
        g = u8(g - vals[5 - i])
        gs = s8(v)
        gs = s8(gs & 0xF7)
        gs = s8(gs + 3)
        gs = s8(-gs)
        a = u8(v)
        a = u8(a + i)
        a = u8(a ^ 0x80)
        sa = s8(v)
        sa = s8(sa - 1)
        sa = s8(sa | (vals[5 - i] & 3))
        m = u8(v + 7)
        m = u8(m << 1)
        arr.append(a)
        sarr.append(sa)
        want.append(f"{g} {gs} {a} {sa} {m} {u8(v * 2 + i)} "
                    f"{s8(s8(v) - 3)} {s8(v) >> 2} {u8(v + 77)} "
                    f"{s8(v ^ 0x5A)}")
    arr = [u8(a + 1) for a in arr]
    sarr = [s8(a ^ 0x0F) for a in sarr]
    want.append(" ".join(f"{a} {b}" for a, b in zip(arr, sarr)) + " 12")
    assert build_and_run(tmp_path, src, "cmpd") == want


CMP_KS = [0, 1, 7, 100, 127, 128, 200, 254, 255, 256, -1, -2, -127, -128,
          -129, 300]


@needs_toolchain
def test_byte_compare_values(tmp_path):
    """Char operands against constants (int and unsigned, either side)
    and against each other, with all six operators."""
    lines, want = [], []
    cmps = ("<", ">", "<=", ">=", "==", "!=")
    for name in ("u8", "s8"):
        _, vals, bits, s = ARRS[name]
        for k in CMP_KS:
            for suffix in ("", "u"):
                if suffix and k < 0:
                    continue
                lit = _lit(k) + suffix
                for op in cmps:
                    for k_left in (False, True):
                        e = (f"{lit} {op} {name}[i]" if k_left
                             else f"{name}[i] {op} {lit}")
                        lines.append(
                            f"for (i = 0; i < {N}; i++) printf(\"%d\", "
                            f"{e}); printf(\"\\n\");")
                        row = ""
                        for v in vals:
                            a, au = _promote(v, bits, s)
                            kk, ku = k, bool(suffix)
                            row += str(_c_cmp(kk, ku, op, a, au) if k_left
                                       else _c_cmp(a, au, op, kk, ku))
                        want.append(row)
    for xa, ya in (("u8", "u8"), ("s8", "s8"), ("u8", "s8"), ("s8", "i16"),
                   ("u8", "u16")):
        _, xv, xb, xs = ARRS[xa]
        _, yv, yb, ys = ARRS[ya]
        for op in cmps:
            for e, swap in ((f"{xa}[i] {op} {ya}[i]", False),
                            (f"{xa}[i] {op} {ya}[(i + 5) % {N}]", True)):
                lines.append(f"for (i = 0; i < {N}; i++) printf(\"%d\", "
                             f"{e}); printf(\"\\n\");")
                row = ""
                for i in range(N):
                    a, au = _promote(xv[i], xb, xs)
                    j = (i + 5) % N if swap else i
                    b, bu = _promote(yv[j], yb, ys)
                    row += str(_c_cmp(a, au, op, b, bu))
                want.append(row)
    # Casts to char and masked values.
    for e, f in (("(signed char)i16[i] >= 16u",
                  lambda v: _c_cmp(wrap(v, 8, True), False, ">=", 16, True)),
                 ("(unsigned char)i16[i] < 128",
                  lambda v: int((v & 0xFF) < 128)),
                 ("(i16[i] & 0x80) == 0", lambda v: int((v & 0x80) == 0)),
                 ("(i16[i] & 0xF0) > 0x70",
                  lambda v: int((v & 0xF0) > 0x70)),
                 ("(signed char)i16[i] < -5",
                  lambda v: int(wrap(v, 8, True) < -5))):
        lines.append(f"for (i = 0; i < {N}; i++) printf(\"%d\", {e}); "
                     "printf(\"\\n\");")
        want.append("".join(str(f(v)) for v in ARRS["i16"][1]))
    _run_lines(tmp_path, _decls(), lines, want, "bcmp")
