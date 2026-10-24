"""Conditions branch on the flags, with no 0/1 value.

if, while, do, for, ?:, && and || used to compute every condition as 0 or
1 in HL (a comparison through a ``jp cc / ld HL,0 / jp / ld HL,1`` ladder,
a signed one with three more jumps for the overflow) and then test HL
again with ``ld A,H / or L``.  Now _gen_branch jumps on the flags of the
test itself:

  * x == 0 and x != 0 are a truth test of x: ``ld A,H / or L``.
  * A comparison of two values known to be 0..255 (or -128..127) is done
    on one byte in A, against a constant with ``cp n``.
  * A 16-bit comparison against a constant is done on the bytes
    (``ld A,L / sub lo / ld A,H / sbc A,hi``), a signed one after the sign
    bits are flipped; x < 0 is ``bit 7,H``.  Two variables are compared on
    the bytes, a signed comparison with ``jp PO / xor 128`` for the
    overflow and then ``jp M`` or ``jp P``.
  * A pointer comparison is unsigned (it was signed, so 0x7000 < 0x9000
    was false).
  * x & K with K in one byte tests that byte only, a single bit with
    ``bit n``; the high byte of a 16-bit variable is read from memory.
  * && and || are jump chains, ! swaps the targets.
  * A 32-bit comparison jumps on the flags of __cmp32.

A comparison used as a value builds 0/1 from the flags.

The unit tests call generate() directly (no AST optimizer, no peephole)
and pin the shape of each fast path.  The end-to-end tests build whole
programs, run them under cpmemu and compare every result with a Python
model of 16-bit-int C.
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
                    enable_const_propagation=False,
                    enable_dead_elimination=False)


def ops(code: str, name: str = "f") -> list[str]:
    """Instruction lines of function ``name``, tab-separated, without
    comments, labels and the frame prologue."""
    start = code.index(f"\n_{name}:")
    end = code.index(f"@{name}_ret:", start)
    out = []
    for line in code[start:end].splitlines():
        if line.startswith("\t") and not line.startswith("\t;"):
            out.append(line.strip().split(";")[0].strip())
    if out[:3] == ["push\tIX", "ld\tIX,0", "add\tIX,SP"]:
        out = out[3:]
    if out and out[-1] == f"jp\t@{name}_ret":
        out.pop()                   # a jump to the very next line
    return out


GLOBALS = ("int x, y; unsigned u, v; unsigned char uc, ub; signed char sc; "
           "char *cp, *cq; long l, m; unsigned long ul; int g(void); ")


def fn(stmts: str, params: str = "void") -> list[str]:
    return ops(gen(GLOBALS + f"int f({params}) {{ {stmts} }} "
                   "int main(void) { return 0; }"))


def cond(c: str, params: str = "void") -> list[str]:
    """The code of ``if (c) return 1; return 2;`` up to the then-branch."""
    o = fn(f"if ({c}) return 1; return 2;", params)
    return o[:o.index("ld\tHL,1")]


NO_VALUE = ("ld\tHL,0", "ld\tHL,1", "dec\tL", "sbc\tA,A")


def no_value(o):
    """No 0/1 value is built and no CMP_T/CMP_F ladder is used."""
    return (not any(i in NO_VALUE for i in o)
            and not any("@CMP_T" in i or "@CMP_F" in i for i in o))


class TestByteConditions:
    def test_uchar_less_than_constant(self):
        assert cond("uc < 64") == ["ld\tA,(_uc)", "cp\t64",
                                   "jp\tNC,@ENDIF2"]

    def test_uchar_greater_is_ge_plus_one(self):
        assert cond("uc > 64") == ["ld\tA,(_uc)", "cp\t65",
                                   "jp\tC,@ENDIF2"]

    def test_signed_char_flips_the_sign(self):
        assert cond("sc > -5") == ["ld\tA,(_sc)", "xor\t128", "cp\t124",
                                   "jp\tC,@ENDIF2"]

    def test_char_truth(self):
        assert cond("uc") == ["ld\tA,(_uc)", "or\tA", "jp\tZ,@ENDIF2"]

    def test_char_equals_zero(self):
        assert cond("uc == 0") == ["ld\tA,(_uc)", "or\tA", "jp\tNZ,@ENDIF2"]

    def test_two_uchars(self):
        assert cond("uc != ub") == ["ld\tA,(_uc)", "ld\tHL,_ub",
                                    "cp\t(HL)", "jp\tZ,@ENDIF2"]

    def test_two_signed_chars(self):
        o = cond("sc < c", params="signed char c")
        assert o == ["ld\tA,(_sc)", "xor\t128", "ld\tD,A", "ld\tA,(IX+4)",
                     "xor\t128", "ld\tE,A", "ld\tA,D", "cp\tE",
                     "jp\tNC,@ENDIF2"]

    def test_uchar_always_true(self):
        # uc >= 0 is always true: no test at all.
        assert cond("uc >= 0") == ["ld\tA,(_uc)"]


class TestWordConditions:
    def test_equal_variables(self):
        assert cond("x == y") == ["ld\tHL,(_x)", "ld\tDE,(_y)", "or\tA",
                                  "sbc\tHL,DE", "jp\tNZ,@ENDIF2"]

    def test_truth(self):
        assert cond("x") == ["ld\tHL,(_x)", "ld\tA,H", "or\tL",
                             "jp\tZ,@ENDIF2"]

    def test_not_equal_zero_is_truth(self):
        assert cond("x != 0") == cond("x")

    def test_equal_small_constant(self):
        assert cond("x == 5") == ["ld\tHL,(_x)", "ld\tA,L", "xor\t5",
                                  "or\tH", "jp\tNZ,@ENDIF2"]

    def test_equal_minus_one(self):
        assert cond("x == -1") == ["ld\tHL,(_x)", "ld\tA,H", "and\tL",
                                   "inc\tA", "jp\tNZ,@ENDIF2"]

    def test_unsigned_less_than_constant(self):
        assert cond("u < 1000") == ["ld\tHL,(_u)", "ld\tA,L", "sub\t232",
                                    "ld\tA,H", "sbc\tA,3", "jp\tNC,@ENDIF2"]

    def test_unsigned_constant_low_byte_zero(self):
        assert cond("u < 0x300") == ["ld\tHL,(_u)", "ld\tA,H", "cp\t3",
                                     "jp\tNC,@ENDIF2"]

    def test_unsigned_greater_than_zero_is_truth(self):
        assert cond("u > 0") == cond("u")

    def test_signed_less_than_zero_is_sign_bit(self):
        assert cond("x < 0") == ["ld\tHL,(_x)", "bit\t7,H", "jp\tZ,@ENDIF2"]

    def test_signed_constant(self):
        # x > 100 is x >= 101; the sign bit is flipped before the borrow.
        assert cond("x > 100") == ["ld\tHL,(_x)", "ld\tA,H", "xor\t128",
                                   "ld\tD,A", "ld\tA,L", "sub\t101",
                                   "ld\tA,D", "sbc\tA,128", "jp\tC,@ENDIF2"]

    def test_signed_variables_overflow_fixup(self):
        o = cond("x < y")
        assert o[:6] == ["ld\tHL,(_x)", "ld\tDE,(_y)", "ld\tA,L", "sub\tE",
                         "ld\tA,H", "sbc\tA,D"]
        assert o[6].startswith("jp\tPO,@CMP_NV")
        assert o[7] == "xor\t128" and o[8].startswith("jp\tP,@ENDIF")

    def test_signed_greater_swaps_the_bytes(self):
        o = cond("x > y")
        assert o[2:6] == ["ld\tA,E", "sub\tL", "ld\tA,D", "sbc\tA,H"]
        assert o[-1].startswith("jp\tP,@ENDIF")

    def test_unsigned_variables(self):
        assert cond("u >= v") == ["ld\tHL,(_u)", "ld\tDE,(_v)", "ld\tA,L",
                                  "sub\tE", "ld\tA,H", "sbc\tA,D",
                                  "jp\tC,@ENDIF2"]

    def test_promoted_uchar_against_int_is_signed_without_ladder(self):
        o = cond("uc < x")
        assert no_value(o)
        assert "jp\tPE" not in " ".join(o)

    def test_pointer_compare_is_unsigned(self):
        o = cond("cp < cq")
        assert "jp\tPO" not in " ".join(o)
        assert o[-1] == "jp\tNC,@ENDIF2"
        assert cond("cp < (char *)0x9000") == ["ld\tHL,(_cp)", "ld\tA,H",
                                               "cp\t144", "jp\tNC,@ENDIF2"]

    def test_null_pointer(self):
        assert cond("cp == (char *)0") == ["ld\tHL,(_cp)", "ld\tA,H",
                                           "or\tL", "jp\tNZ,@ENDIF2"]


class TestBitTests:
    def test_low_byte_bit_of_a_global(self):
        assert cond("x & 4") == ["ld\tA,(_x)", "and\t4", "jp\tZ,@ENDIF2"]

    def test_high_byte_bit_of_a_global(self):
        assert cond("x & 0x4000") == ["ld\tA,(_x+1)", "and\t64",
                                      "jp\tZ,@ENDIF2"]

    def test_bit_of_a_parameter(self):
        assert cond("p & 0x100", params="int p") == ["bit\t0,(IX+5)",
                                                     "jp\tZ,@ENDIF2"]
        assert cond("p & 8", params="int p") == ["bit\t3,(IX+4)",
                                                 "jp\tZ,@ENDIF2"]

    def test_bit_of_a_value(self):
        assert cond("g() & 0x200") == ["call\t_g", "bit\t1,H",
                                       "jp\tZ,@ENDIF2"]

    def test_not_bit(self):
        assert cond("!(x & 4)") == ["ld\tA,(_x)", "and\t4", "jp\tNZ,@ENDIF2"]
        assert cond("(x & 4) == 0") == cond("!(x & 4)")

    def test_both_bytes(self):
        assert cond("x & 0x1FF") == ["ld\tHL,(_x)", "ld\tA,H", "and\t1",
                                     "or\tL", "jp\tZ,@ENDIF2"]


class TestJumpChains:
    def test_and_is_two_jumps_to_else(self):
        o = cond("x < 0 && uc")
        assert o == ["ld\tHL,(_x)", "bit\t7,H", "jp\tZ,@ENDIF2",
                     "ld\tA,(_uc)", "or\tA", "jp\tZ,@ENDIF2"]

    def test_or_skips_on_true(self):
        o = cond("x || u")
        assert o == ["ld\tHL,(_x)", "ld\tA,H", "or\tL", "jp\tNZ,@OR_S3",
                     "ld\tHL,(_u)", "ld\tA,H", "or\tL", "jp\tZ,@ENDIF2"]

    def test_not_of_and(self):
        o = cond("!(uc && ub)")
        assert no_value(o)
        # Skip the then-branch when both are true: the last jump is NZ.
        assert o[-1] == "jp\tNZ,@ENDIF2"

    def test_logical_value_keeps_labels(self):
        code = gen(GLOBALS + "int f(void) { return x < y && uc; } "
                   "int main(void) { return 0; }")
        o = ops(code)
        assert "@AND_F" in code and "@AND_E" in code
        # The operands jump on their flags; only the result is 0/1.
        assert o.count("ld\tHL,1") == 1 and o.count("ld\tHL,0") == 1

    def test_while_loop(self):
        o = fn("while (uc < 10 && x) uc++; return 0;")
        assert o[:3] == ["ld\tA,(_uc)", "cp\t10", "jp\tNC,@ENDWHILE2"]

    def test_do_while_jumps_back(self):
        o = fn("do { uc++; } while (uc != 7); return 0;")
        assert o[-3:-1] == ["cp\t7", "jp\tNZ,@DO1"]

    def test_for_loop(self):
        o = fn("int n = 0; for (uc = 0; uc < 200; uc++) n++; return n;")
        i = o.index("cp\t200")
        assert o[i - 1] == "ld\tA,(_uc)" and o[i + 1].startswith("jp\tNC,")

    def test_ternary(self):
        o = fn("return u <= v ? 3 : 4;")
        assert o[:7] == ["ld\tHL,(_u)", "ld\tDE,(_v)", "ld\tA,E", "sub\tL",
                         "ld\tA,D", "sbc\tA,H", "jp\tC,@TERN_E1"]

    def test_long_compare(self):
        o = cond("l < m")
        # F7 compares a long inline: a 32-bit subtract, no helper call.
        assert "call\t__cmp32" not in o and "sbc\tHL,BC" in o
        assert no_value(o) and "ld\tDE,0" not in o

    def test_long_zero_is_truth(self):
        assert cond("l != 0") == ["ld\tHL,(_l)", "ld\tDE,(_l+2)", "ld\tA,H",
                                  "or\tL", "or\tE", "or\tD", "jp\tZ,@ENDIF2"]

    def test_constant_condition_unchanged(self):
        # A constant keeps the plain test (the AST optimizer and the
        # peephole fold it).
        code = gen("int main(void) { if (1) return 1; return 0; }")
        assert "jp\tZ,@ENDIF" in code


class TestValues:
    def test_compare_value_from_carry(self):
        assert fn("return u < v;") == ["ld\tHL,(_u)", "ld\tDE,(_v)",
                                       "ld\tA,L", "sub\tE", "ld\tA,H",
                                       "sbc\tA,D", "sbc\tA,A", "and\t1",
                                       "ld\tL,A", "ld\tH,0"]

    def test_compare_value_from_flag(self):
        o = fn("return x == y;")
        i = o.index("ld\tHL,1")
        assert o[i + 1] == "jp\tZ,@CMP_E1" and o[i + 2] == "dec\tL"


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


def wrap(v, bits, signed):
    v &= (1 << bits) - 1
    if signed and v >> (bits - 1):
        v -= 1 << bits
    return v


# name: (C type, bits, signed, values)
TYPES = {
    "u8": ("unsigned char", 8, False, [0, 1, 127, 128, 200, 255]),
    "s8": ("signed char", 8, True, [0, 1, -1, 127, -128, -2]),
    "i16": ("int", 16, True, [0, 1, -1, 255, 256, 32767, -32768, -300]),
    "u16": ("unsigned", 16, False, [0, 1, 255, 256, 0x7FFF, 0x8000,
                                    0xFFFF, 0x1234]),
    "i32": ("long", 32, True, [0, 1, -1, 65535, 65536, -65536,
                               0x7FFFFFFF, -0x80000000]),
    "u32": ("unsigned long", 32, False, [0, 1, 0xFFFF, 0x10000,
                                         0x80000000, 0xFFFFFFFF]),
}

OPS = ("<", ">", "<=", ">=", "==", "!=")


def _common(ta, tb):
    """(bits, signed) of the comparison after the usual conversions."""
    _, ba, sa, _ = TYPES[ta]
    _, bb, sb, _ = TYPES[tb]
    ba, sa = (16, True) if ba < 16 else (ba, sa)
    bb, sb = (16, True) if bb < 16 else (bb, sb)
    if ba == bb:
        return ba, sa and sb
    big = (ba, sa) if ba > bb else (bb, sb)
    return big            # a long holds every unsigned int


def _cmp(a, op, b, bits, signed):
    a, b = wrap(a, bits, signed), wrap(b, bits, signed)
    return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b,
            "==": a == b, "!=": a != b}[op]


def _mask(a, b, bits, signed):
    """The 16 result bits of the body made by _cmp_body."""
    r = 0
    c = {op: _cmp(a, op, b, bits, signed) for op in OPS}
    for i, op in enumerate(OPS):
        r |= c[op] << i
        r |= c[op] << (6 + i)
    r |= (c["<"] or c["=="]) << 12
    r |= (c[">"] or (c["<"] and c["=="])) << 13
    r |= c["<="] << 14
    r |= c["!="] << 15
    return r


def _cmp_body(a, b):
    """Statements that set 16 bits of r from comparisons of a and b in
    every context: if, value, ! with ||, && with ||, ?: and do-while."""
    s = ["unsigned r = 0; int n = 0;"]
    for i, op in enumerate(OPS):
        s.append(f"if ({a} {op} {b}) r |= {1 << i}u;")
    for i, op in enumerate(OPS):
        s.append(f"r |= (unsigned)({a} {op} {b}) << {6 + i};")
    s.append(f"if (!({a} >= {b}) || {a} == {b}) r |= 0x1000u;")
    s.append(f"if ({a} > {b} || ({a} < {b} && {a} == {b})) r |= 0x2000u;")
    s.append(f"r |= ({a} <= {b} ? 1u : 0u) << 14;")
    s.append(f"do {{ n++; }} while (n < 2 && {a} != {b});")
    s.append("if (n == 2) r |= 0x8000u;")
    s.append("return r;")
    return " ".join(s)


def _pair_program(pairs):
    """Functions comparing parameters, globals, and values through
    pointers, for each type pair; main calls each with every pair of
    values and prints the results."""
    decls, calls, want = [], [], []
    for n, (ta, tb) in enumerate(pairs):
        ca, cb = TYPES[ta][0], TYPES[tb][0]
        va, vb = TYPES[ta][3], TYPES[tb][3]
        bits, signed = _common(ta, tb)
        decls.append(f"{ca} va{n}[] = {{{', '.join(f'{v}L' if abs(v) > 32767 else str(v) for v in va)}}};")
        decls.append(f"{cb} vb{n}[] = {{{', '.join(f'{v}L' if abs(v) > 32767 else str(v) for v in vb)}}};")
        decls.append(f"{ca} ga{n}; {cb} gb{n};")
        decls.append(f"unsigned fp{n}({ca} a, {cb} b) {{ {_cmp_body('a', 'b')} }}")
        decls.append(f"unsigned fg{n}(void) {{ {_cmp_body(f'ga{n}', f'gb{n}')} }}")
        decls.append(f"unsigned fd{n}({ca} *pa, {cb} *pb) "
                     f"{{ {_cmp_body('*pa', '*pb')} }}")
        calls.append(
            f"for (i = 0; i < {len(va)}; i++) for (j = 0; j < {len(vb)}; j++) "
            f"{{ ga{n} = va{n}[i]; gb{n} = vb{n}[j]; "
            f"printf(\"%u %u %u\\n\", fp{n}(va{n}[i], vb{n}[j]), fg{n}(), "
            f"fd{n}(&va{n}[i], &vb{n}[j])); }}")
        for a in va:
            for b in vb:
                m = _mask(a, b, bits, signed)
                want.append(f"{m} {m} {m}")
    src = ("\n".join(decls) + "\nint main(void) { int i, j; "
           + " ".join(calls) + " return 0; }")
    return src, want


def _check(tmp_path, src, want, name):
    got = build_and_run(tmp_path, src, name)
    assert len(got) == len(want), (name, got[-3:])
    bad = [(i, w, g) for i, (w, g) in enumerate(zip(want, got)) if w != g]
    assert not bad, (name, bad[:5])


@needs_toolchain
def test_compare_pairs_16(tmp_path):
    """Every 8- and 16-bit type pair, all six operators, every context."""
    names = ("u8", "s8", "i16", "u16")
    pairs = [(a, b) for a in names for b in names]
    for k in range(0, len(pairs), 8):
        src, want = _pair_program(pairs[k:k + 8])
        _check(tmp_path, src, want, f"pairs16_{k}")


@needs_toolchain
def test_compare_pairs_32(tmp_path):
    """Pairs with a 32-bit operand."""
    pairs = [("i32", "i32"), ("u32", "u32"), ("i32", "u32"), ("i32", "i16"),
             ("u16", "i32"), ("u32", "u16"), ("u8", "u32"), ("s8", "i32")]
    for k in range(0, len(pairs), 4):
        src, want = _pair_program(pairs[k:k + 4])
        _check(tmp_path, src, want, f"pairs32_{k}")


# Constants of type int (in -32768..32767) and unsigned int (the hex ones
# above 0x7FFF, and the u ones).
CONSTS = [("0", 0, False), ("1", 1, False), ("2", 2, False),
          ("127", 127, False), ("128", 128, False), ("255", 255, False),
          ("256", 256, False), ("300", 300, False), ("0x1234", 0x1234, False),
          ("32767", 32767, False), ("0x8000", 0x8000, True),
          ("0xFF00", 0xFF00, True), ("0xFFFF", 0xFFFF, True),
          ("(-1)", -1, False), ("(-2)", -2, False), ("(-128)", -128, False),
          ("(-129)", -129, False), ("(-256)", -256, False),
          ("(-32767-1)", -32768, False), ("200u", 200, True),
          ("1u", 1, True)]


def _const_cmp(a, ta, op, k, ku, left):
    """``a op K`` (or ``K op a`` when left) in C."""
    _, bits, signed, _ = TYPES[ta]
    if bits < 16:
        bits, signed = 16, True
    if bits == 16:
        signed = signed and not ku
    if left:
        return _cmp(k, op, a, bits, signed)
    return _cmp(a, op, k, bits, signed)


@needs_toolchain
def test_compare_constants(tmp_path):
    """Each type against each constant, the constant on either side, in
    an if and as a value."""
    for ta in ("u8", "s8", "i16", "u16"):
        ct, _, _, vals = TYPES[ta]
        decls, calls, want = [f"{ct} vals[] = {{{', '.join(map(str, vals))}}};"
                              f" {ct} gv;"], [], []
        for n, (lit, k, ku) in enumerate(CONSTS):
            body = ["unsigned long r = 0;"]
            bit = 0
            for left in (False, True):
                for op in OPS:
                    e = f"{lit} {op} a" if left else f"a {op} {lit}"
                    body.append(f"if ({e}) r |= {1 << bit}UL;")
                    body.append(f"if (({e}) && gv == a) r |= {1 << (bit + 12)}UL;")
                    bit += 1
            decls.append(f"unsigned long k{n}({ct} a) {{ {' '.join(body)} "
                         "return r; }")
            calls.append(f"for (i = 0; i < {len(vals)}; i++) {{ gv = vals[i]; "
                         f"printf(\"%lu\\n\", k{n}(vals[i])); }}")
            for a in vals:
                r, bit = 0, 0
                for left in (False, True):
                    for op in OPS:
                        c = _const_cmp(a, ta, op, k, ku, left)
                        r |= c << bit
                        r |= c << (bit + 12)
                        bit += 1
                want.append(str(r))
        src = ("\n".join(decls) + "\nint main(void) { int i; "
               + " ".join(calls) + " return 0; }")
        _check(tmp_path, src, want, f"consts_{ta}")


BIT_KS = [1, 2, 4, 0x80, 0x100, 0x4000, 0x8000, 3, 0xF0, 0xFF, 0x300,
          0x1FF, 0xFF00, 0x180, 0xFFFF]


@needs_toolchain
def test_bit_tests(tmp_path):
    """if (x & K) and if (!(x & K)) for a parameter, a local, a global and
    a value through a pointer."""
    decls, calls, want = [], [], []
    for ta in ("u8", "s8", "i16", "u16"):
        ct, bits, signed, vals = TYPES[ta]
        decls.append(f"{ct} b_{ta}[] = {{{', '.join(map(str, vals))}}}; "
                     f"{ct} bg_{ta};")
        body = [f"unsigned long r = 0; {ct} l = a; bg_{ta} = a;"]
        for i, k in enumerate(BIT_KS):
            kk = hex(k)
            if i < 8:
                body.append(f"if (a & {kk}) r |= {1 << i}UL;")
                body.append(f"if (!(l & {kk})) r |= {1 << (i + 8)}UL;")
            body.append(f"if ((bg_{ta} & {kk}) && (*p & {kk})) "
                        f"r |= {1 << (i + 16)}UL;")
        decls.append(f"unsigned long bt_{ta}({ct} a, {ct} *p) "
                     f"{{ {' '.join(body)} return r; }}")
        calls.append(f"for (i = 0; i < {len(vals)}; i++) "
                     f"printf(\"%lu\\n\", bt_{ta}(b_{ta}[i], &b_{ta}[i]));")
        for a in vals:
            p = a & 0xFFFF            # promoted to 16 bits
            r = 0
            for i, k in enumerate(BIT_KS):
                t = bool(p & k)
                if i < 8:
                    r |= t << i
                    r |= (not t) << (i + 8)
                r |= t << (i + 16)
            want.append(str(r))
    src = ("\n".join(decls) + "\nint main(void) { int i; "
           + " ".join(calls) + " return 0; }")
    _check(tmp_path, src, want, "bits")


@needs_toolchain
def test_pointer_compares(tmp_path):
    """Pointers compare as unsigned addresses, also across 0x8000."""
    addrs = [0, 0x100, 0x7FFF, 0x8000, 0x9000, 0xFFFF]
    src = ("char *pv[] = {" + ", ".join(f"(char *){hex(a)}" for a in addrs)
           + "};\nunsigned f(char *a, char *b) { " + _cmp_body("a", "b")
           + " }\nunsigned z(char *a) { unsigned r = 0; "
           "if (a) r |= 1; if (!a) r |= 2; if (a == 0) r |= 4; "
           "if (a != (char *)0) r |= 8; if (a < (char *)0x8000) r |= 16; "
           "if (a >= (char *)0x9000) r |= 32; return r; }\n"
           "int main(void) { int i, j; for (i = 0; i < 6; i++) { "
           "for (j = 0; j < 6; j++) printf(\"%u \", f(pv[i], pv[j])); "
           "printf(\"%u\\n\", z(pv[i])); } return 0; }")
    want = []
    for a in addrs:
        row = [str(_mask(a, b, 16, False)) for b in addrs]
        z = ((a != 0) | (a == 0) << 1 | (a == 0) << 2 | (a != 0) << 3
             | (a < 0x8000) << 4 | (a >= 0x9000) << 5)
        want.append(" ".join(row) + f" {z}")
    _check(tmp_path, src, want, "ptrs")


@needs_toolchain
def test_loops(tmp_path):
    """Loop conditions that count."""
    src = r"""
unsigned char uc; signed char sc; int x; unsigned u;
int main(void) {
    int n = 0;
    for (uc = 0; uc < 200; uc++) n++;
    printf("%d\n", n);
    n = 0; sc = 100;
    while (sc > -100) { sc -= 3; n++; }
    printf("%d %d\n", n, sc);
    n = 0; x = -1000;
    do { x += 77; n++; } while (x < 1000 && n < 100);
    printf("%d %d\n", n, x);
    n = 0;
    for (u = 0xFFF0u; u >= 0x8000u && u != 5; u += 3) n++;
    printf("%d %u\n", n, u);
    n = 0;
    for (x = 0; !(x & 0x100); x += 7) n++;
    printf("%d %d\n", n, x);
    return 0;
}
"""
    n2 = 0
    sc = 100
    while sc > -100:
        sc -= 3
        n2 += 1
    x, n3 = -1000, 0
    while True:
        x += 77
        n3 += 1
        if not (x < 1000 and n3 < 100):
            break
    u, n4 = 0xFFF0, 0
    while u >= 0x8000 and u != 5:
        n4 += 1
        u = (u + 3) & 0xFFFF
    x5, n5 = 0, 0
    while not x5 & 0x100:
        n5 += 1
        x5 += 7
    want = ["200", f"{n2} {sc}", f"{n3} {x}", f"{n4} {u}", f"{n5} {x5}"]
    _check(tmp_path, src, want, "loops")
