"""32-bit operations done inline (F7): + - & | ^, comparisons and tests
with a constant or a variable operand, casts int <-> long, shifts by a
constant, and the operand staging for what still calls a helper.

Before, every long operation staged the right operand in __tmp32 (through
DEHL) and called __add32, __and32, __cmp32 and so on, a long operand that
was an int went through two __sext32 calls, and a nested right operand was
copied back with a thirteen-instruction stack shuffle.  Now:

  * ``x + K``, ``x - K``: ``ld BC,lo / add HL,BC`` and a carry step on DE
    (``jr NC / inc DE`` when the high word of K is 0, ``jr C / dec DE``
    when it is FFFF);
  * ``x + v``, ``x - v``, v a variable (1, 2 or 4 bytes, any signedness):
    BC loaded from memory, ``add HL,BC`` / ``adc HL,BC`` through DE;
  * ``x & K``, ``x | K``, ``x ^ K``: per byte, with the 00 and FF bytes
    skipped or turned into ``ld r,0`` / ``ld r,255``; with a variable per
    byte straight from memory;
  * comparisons with a constant: a borrow chain on L, H, E, D with the
    constant bytes as immediates (the sign bit flipped in B for a signed
    compare), equality as an XOR chain; with a variable: ``sbc HL,BC``
    through DE (or the bytes of the variable in A) -- jumps on the flags,
    no __cmp32;
  * ``x & K`` as a condition tests only the bytes K selects, ``bit n,r``
    for one bit;
  * int -> long is ``ld A,H / rlca / sbc A,A / ld E,A / ld D,A`` or
    ``ld DE,0`` inline, and only once;
  * shifts by a constant 1..4 are single shifts; 5..7 are a shift the
    other way into a fifth byte and a byte move;
  * a right operand that is a constant or a variable is read after the
    left operand, into __tmp32 through BC; a nested right operand waits on
    the stack as two pushes and comes back through BC.

The unit tests call generate() directly and pin the shapes.  The
end-to-end tests build programs from one expression each, in every
combination of operand kinds, run all of them under cpmemu over a set of
edge values and compare a checksum with a Python model of C.
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


GLOBALS = ("long l, l2; unsigned long ul, ul2; int i; unsigned u; "
           "signed char sc; unsigned char uc; int r; long lr; "
           "unsigned long ulr; ")


def fn(body: str, params: str = "void", rtype: str = "void") -> list[str]:
    """Instruction lines of f, tab-separated, without comments, labels
    and the frame prologue/epilogue."""
    code = gen(GLOBALS + f"{rtype} f({params}) {{ {body} }} "
               "int main(void) { return 0; }")
    start = code.index("\n_f:")
    end = code.index("@f_ret:", start)
    out = []
    for line in code[start:end].splitlines()[2:]:
        line = line.split(";")[0].strip()
        if not line or line.endswith(":"):
            continue
        out.append("\t".join(line.split(None, 1)).replace(", ", ","))
    return out


def no_helper(o: list[str]) -> bool:
    return not any(x.startswith("call") for x in o)


class TestAddSub:
    def test_add_small_constant(self):
        assert fn("ulr = ul + 5;") == [
            "ld\tHL,(_ul)", "ld\tDE,(_ul+2)", "ld\tBC,5", "add\tHL,BC",
            "jr\tNC,@AD1", "inc\tDE", "ld\t(_ulr),HL", "ld\t(_ulr+2),DE"]

    def test_sub_small_constant_is_add_of_the_negative(self):
        o = fn("lr = l - 3;")
        assert o[2:6] == ["ld\tBC,65533", "add\tHL,BC", "jr\tC,@AD1",
                          "dec\tDE"]

    def test_add_wide_constant_carries_through_adc(self):
        o = fn("lr = l + 100000L;")
        assert o[2:] == ["ld\tBC,34464", "add\tHL,BC", "ex\tDE,HL",
                         "ld\tBC,1", "adc\tHL,BC", "ex\tDE,HL",
                         "ld\t(_lr),HL", "ld\t(_lr+2),DE"]

    def test_add_constant_with_zero_low_word_touches_only_DE(self):
        o = fn("ulr = ul + 0x20000UL;")
        assert o[2:4] == ["inc\tDE", "inc\tDE"]
        assert no_helper(o) and "add\tHL,BC" not in o

    def test_add_variable_reads_memory_into_BC(self):
        o = fn("ulr = ul + ul2;")
        assert o[:9] == ["ld\tHL,(_ul)", "ld\tDE,(_ul+2)", "ld\tBC,(_ul2)",
                         "add\tHL,BC", "ex\tDE,HL", "ld\tBC,(_ul2+2)",
                         "adc\tHL,BC", "ex\tDE,HL", "ld\t(_ulr),HL"]
        assert "(__tmp32)" not in " ".join(o)

    def test_add_int_variable_is_sign_extended_in_place(self):
        o = fn("lr = l + i;")
        assert no_helper(o)
        assert "ld\tC,A" in o and "ld\tB,A" in o and "adc\tHL,BC" in o

    def test_add_unsigned_char_variable(self):
        o = fn("ulr = ul + uc;")
        assert no_helper(o) and "jr\tNC,@AD1" in o

    def test_sub_variable_clears_the_carry_first(self):
        o = fn("lr = l - l2;")
        assert o[2:4] == ["ld\tBC,(_l2)", "or\tA"]
        assert "sbc\tHL,BC" in o and no_helper(o)

    def test_int_operand_is_extended_once(self):
        o = fn("lr = i + l;")
        assert no_helper(o)
        assert o.count("rlca") == 1


class TestBitwise:
    def test_xor_constant_by_byte(self):
        o = fn("ulr = ul ^ 0xEDB88320UL;")
        assert o[2:] == ["ld\tA,L", "xor\t32", "ld\tL,A", "ld\tA,H",
                         "xor\t131", "ld\tH,A", "ld\tA,E", "xor\t184",
                         "ld\tE,A", "ld\tA,D", "xor\t237", "ld\tD,A",
                         "ld\t(_ulr),HL", "ld\t(_ulr+2),DE"]

    def test_and_with_zero_bytes_clears_the_registers(self):
        o = fn("ulr = ul & 0xFFFFUL;")
        assert o[2:] == ["ld\tDE,0", "ld\t(_ulr),HL", "ld\t(_ulr+2),DE"]
        o = fn("ulr = ul & 0xFF00FFUL;")
        assert o[2:4] == ["ld\tH,0", "ld\tD,0"]

    def test_or_high_bit_touches_one_byte(self):
        o = fn("ulr = ul | 0x80000000UL;")
        assert o[2:5] == ["ld\tA,D", "or\t128", "ld\tD,A"]

    def test_xor_ff_is_cpl(self):
        o = fn("ulr = ul ^ 0xFF;")
        assert o[2:5] == ["ld\tA,L", "cpl", "ld\tL,A"]

    def test_variable_operand_per_byte(self):
        o = fn("ulr = ul | ul2;")
        assert o[2:6] == ["ld\tA,(_ul2)", "or\tL", "ld\tL,A", "ld\tA,(_ul2+1)"]
        assert no_helper(o)

    def test_unsigned_short_variable_and_clears_high_word(self):
        o = fn("ulr = ul & u;")
        assert "ld\tE,0" in o and "ld\tD,0" in o and no_helper(o)

    def test_signed_short_variable_is_extended_then_anded(self):
        # The extension bytes of a signed int are computed, so the int is
        # the value in DEHL and the long variable is the memory operand.
        o = fn("ulr = ul & i;")
        assert no_helper(o) and o.count("rlca") == 1
        assert "ld\tA,(_ul)" in o and "and\tL" in o


class TestCompare:
    def test_equal_constant_xor_chain(self):
        o = fn("r = ul == 0x12345678UL;")
        assert "call\t__cmp32" not in o
        assert o[2:10] == ["ld\tA,L", "xor\t120", "ld\tB,A", "ld\tA,H",
                           "xor\t86", "or\tB", "ld\tB,A", "ld\tA,E"]

    def test_signed_less_than_constant_flips_the_sign_in_B(self):
        o = fn("r = l < 1000L;")
        assert o[2:7] == ["ld\tA,D", "xor\t128", "ld\tB,A", "ld\tA,L",
                          "sub\t232"]
        assert "sbc\tA,128" in o and no_helper(o)

    def test_unsigned_less_than_power_of_two_tests_only_the_top(self):
        o = fn("r = ul < 0x1000000UL;")
        assert o[2:4] == ["ld\tA,D", "cp\t1"]

    def test_signed_less_than_zero_is_the_sign_bit(self):
        o = fn("if (l < 0) r = 1;")
        assert o[2] == "bit\t7,D"

    def test_unsigned_less_than_one_is_zero_test(self):
        o = fn("if (ul < 1) r = 1;")
        assert o[2:6] == ["ld\tA,H", "or\tL", "or\tE", "or\tD"]

    def test_variable_compare_is_a_borrow_chain_in_HL_DE(self):
        o = fn("if (ul < ul2) r = 1;")
        assert o[2:8] == ["ld\tBC,(_ul2)", "or\tA", "sbc\tHL,BC",
                          "ex\tDE,HL", "ld\tBC,(_ul2+2)", "sbc\tHL,BC"]
        assert o[8].startswith("jp\tNC") and no_helper(o)

    def test_greater_than_variable_reads_the_bytes_of_the_variable(self):
        o = fn("if (ul > ul2) r = 1;")
        assert o[2:6] == ["ld\tA,(_ul2)", "sub\tL", "ld\tA,(_ul2+1)",
                          "sbc\tA,H"]
        assert no_helper(o)

    def test_signed_variable_compare_fixes_the_overflow(self):
        o = fn("if (l < l2) r = 1;")
        assert any(x.startswith("jp\tPO,@CMP_NV") for x in o)
        assert "xor\t128" in o and "ld\tA,H" in o and no_helper(o)

    def test_compare_value_clears_DE(self):
        o = fn("r = ul < ul2;")
        assert o[-3:] == ["ld\tDE,0", "ld\t(_r),HL", ] or "ld\tDE,0" in o

    def test_and_condition_tests_one_bit(self):
        assert fn("if (ul & 1) r = 1;")[:2] == ["ld\tA,(_ul)", "and\t1"]
        assert fn("if (ul & 0x80000000UL) r = 1;")[0] == "ld\tA,(_ul+3)"

    def test_and_condition_of_a_local_uses_bit(self):
        code = gen("unsigned long g(unsigned long a, int k)"
                   "{ if (a & 0x100UL) return 1; return 2; }"
                   "int main(void){ return g(1, 2) != 0; }")
        assert "bit\t0,(IX+5)" in code


class TestCasts:
    def test_int_to_long_is_inline(self):
        o = fn("lr = i;")
        assert o[:6] == ["ld\tHL,(_i)", "ld\tA,H", "rlca", "sbc\tA,A",
                         "ld\tE,A", "ld\tD,A"]

    def test_unsigned_to_long_is_ld_de_0(self):
        assert fn("lr = u;")[:2] == ["ld\tHL,(_u)", "ld\tDE,0"]

    def test_unsigned_char_to_unsigned_long(self):
        o = fn("ulr = uc;")
        assert o.count("ld\tDE,0") == 1 and no_helper(o)


class TestShifts:
    def test_shift_left_4_is_inline(self):
        o = fn("ulr = ul << 4;")
        assert no_helper(o) and o.count("rl\tE") == 4

    def test_shift_right_7_uses_a_fifth_byte(self):
        o = fn("ulr = ul >> 7;")
        assert no_helper(o)
        assert o[2] == "xor\tA" and o.count("rla") == 1
        assert o[-4:-2] == ["ld\tD,A", "ld\t(_ulr),HL"] or "ld\tD,A" in o

    def test_shift_left_6_uses_a_fifth_byte(self):
        o = fn("ulr = ul << 6;")
        assert no_helper(o) and o.count("rra") == 2

    def test_signed_shift_right_6(self):
        o = fn("lr = l >> 6;")
        assert no_helper(o) and o.count("rla") == 2
        assert "sbc\tA,A" in o


class TestStaging:
    def test_variable_shift_count_is_read_into_A(self):
        o = fn("ulr = ul >> i;")
        assert "call\t__shr32" in o
        assert "(__tmp32)" not in " ".join(o)

    def test_variable_multiplier_is_staged_through_BC(self):
        o = fn("ulr = ul * ul2;")
        assert o[2:6] == ["ld\tBC,(_ul2)", "ld\t(__tmp32),BC",
                          "ld\tBC,(_ul2+2)", "ld\t(__tmp32+2),BC"]
        assert o[6] == "call\t__mul32"

    def test_both_complex_division_uses_two_pushes_and_pops_through_BC(self):
        code = gen("void g(void); long x,y,z,w,r;"
                   "int main(void){ r = (g(), x/y) / (z*w); return 0; }")
        assert "push\tDE\n\tpush\tHL" in code
        assert ("pop\tBC\n\tld\t(__tmp32),BC\n\tpop\tBC\n"
                "\tld\t(__tmp32+2),BC") in code


class TestStackOperand:
    """+ - & | ^ and compares with a nested right operand pop that value
    from the stack: no __tmp32, no helper."""

    def test_add_of_two_nested_operands(self):
        o = fn("lr = (l ^ l2) + (ul2 * ul);")
        assert o.count("push\tDE") == 1 and o.count("push\tHL") == 1
        assert o[-8:-2] == ["pop\tBC", "add\tHL,BC", "ex\tDE,HL",
                            "pop\tBC", "adc\tHL,BC", "ex\tDE,HL"]
        assert "call\t__add32" not in o

    def test_xor_of_two_nested_operands(self):
        o = fn("ulr = (ul + ul2) ^ (ul2 * ul);")
        i = o.index("pop\tBC")
        assert o[i:i + 7] == ["pop\tBC", "ld\tA,C", "xor\tL", "ld\tL,A",
                              "ld\tA,B", "xor\tH", "ld\tH,A"]
        assert "call\t__xor32" not in o

    def test_compare_of_two_nested_operands(self):
        o = fn("if ((ul + ul2) < (ul2 * ul)) r = 1;")
        assert "call\t__cmp32" not in o
        i = o.index("pop\tBC")
        assert o[i:i + 3] == ["pop\tBC", "or\tA", "sbc\tHL,BC"]

    def test_greater_compares_the_bytes_popped_from_the_stack(self):
        o = fn("if ((ul + ul2) > (ul2 * ul)) r = 1;")
        i = o.index("pop\tBC")
        assert o[i:i + 5] == ["pop\tBC", "ld\tA,C", "sub\tL", "ld\tA,B",
                              "sbc\tA,H"]


class TestInPlaceCompound:
    """``x op= K`` and ``x op= v`` as statements update a 4-byte variable
    where it is, with no DEHL round trip."""

    def test_or_one_byte(self):
        assert fn("ul |= 0x80000000UL;") == [
            "ld\tA,(_ul+3)", "or\t128", "ld\t(_ul+3),A"]

    def test_and_clears_bytes_with_one_xor_a(self):
        o = fn("ul &= 0xFF0000FFUL;")
        assert o == ["xor\tA", "ld\t(_ul+1),A", "ld\t(_ul+2),A"]

    def test_xor_ff_is_cpl_in_memory(self):
        assert fn("ul ^= 0xFF;") == ["ld\tA,(_ul)", "cpl", "ld\t(_ul),A"]

    def test_add_small_constant_touches_the_high_word_on_carry_only(self):
        o = fn("ul += 7;")
        assert o == ["ld\tHL,(_ul)", "ld\tBC,7", "add\tHL,BC",
                     "ld\t(_ul),HL", "jr\tNC,@AD1", "ld\tHL,(_ul+2)",
                     "inc\tHL", "ld\t(_ul+2),HL"]

    def test_add_variable_word_by_word(self):
        o = fn("ul += ul2;")
        assert o == ["ld\tHL,(_ul)", "ld\tBC,(_ul2)", "add\tHL,BC",
                     "ld\t(_ul),HL", "ld\tHL,(_ul+2)", "ld\tBC,(_ul2+2)",
                     "adc\tHL,BC", "ld\t(_ul+2),HL"]

    def test_x_equals_x_op_k_is_the_same(self):
        assert fn("ul = ul + 7;") == fn("ul += 7;")
        assert fn("ul = ul ^ 0x80000000UL;") == fn("ul ^= 0x80000000UL;")

    def test_value_used_keeps_the_register_form(self):
        o = fn("return ul += 7;", rtype="unsigned long")
        assert "ld\tHL,(_ul)" in o and "ld\tDE,(_ul+2)" in o

    def test_local_in_ix_frame(self):
        code = gen("unsigned long g(unsigned long a) { a ^= 0x100UL; "
                   "return a; } int main(void) { return g(1) != 0; }")
        assert "ld\tA,(IX+5)" in code and "ld\t(IX+5),A" in code


class TestMisc:
    def test_constant_minus_computed_value_has_no_stack(self):
        o = fn("lr = 32L - (l ^ l2);")
        i = o.index("ld\tA,32")
        assert o[i:i + 8] == ["ld\tA,32", "sub\tL", "ld\tL,A", "ld\tA,0",
                              "sbc\tA,H", "ld\tH,A", "ld\tA,0",
                              "sbc\tA,E"]
        assert not any(x.startswith(("push", "pop")) for x in o)

    def test_computed_shift_count_waits_as_one_word(self):
        o = fn("ulr = ul << (i & 7);")
        assert "push\tHL" in o and "push\tDE" not in o
        assert o[o.index("pop\tBC") + 1] == "ld\tA,C"
        assert "call\t__shl32" in o

    def test_long_index_loads_only_the_low_word(self):
        code = gen("long idx; int tab[10]; int f(void) { return tab[idx]; }"
                   "int main(void){ return f(); }")
        i = code.index("\n_f:")
        text = code[i:code.index("@f_ret:", i)]
        assert "ld\tHL,(_idx)" in text and "(_idx+2)" not in text


class TestExtension:
    def test_an_extension_before_an_extension_is_dropped(self):
        o = fn("ulr = ul ^ (unsigned char)(ul2 + 1);")
        assert o.count("rlca") == 0 and no_helper(o)


# ---------------------------------------------------------------------
# End-to-end: one expression per test function, all operand kinds, a
# checksum over edge values compared with a Python model.
# ---------------------------------------------------------------------

needs_toolchain = pytest.mark.skipif(
    not (CPMEMU.exists() and shutil.which("um80") and shutil.which("ul80")
         and (LIB_DIR / "libc.lib").exists()),
    reason="needs cpmemu, um80, ul80 and the built libraries")


def build_and_run(tmp_path, source, name="prog", flags=()):
    c_file = tmp_path / (name + ".c")
    c_file.write_text("#include <stdio.h>\n" + source)
    mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "--printf", "long", "--no-inlining",
                        "--no-const-propagation", *flags, "-o", str(mac)],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    env = dict(os.environ, PYTHONHASHSEED="0")
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=240, env=env)
    out = run.stdout.decode("latin-1").replace("\r", "")
    return [l.strip() for l in out.splitlines()
            if l.strip() and not l.startswith(("CPU mode", "Loaded",
                                               "Program exit"))]


def wrap(v, bits, signed):
    v &= (1 << bits) - 1
    if signed and v >> (bits - 1):
        v -= 1 << bits
    return v


# name: (C type, bits, signed)
KINDS = {
    "L": ("long", 32, True),
    "UL": ("unsigned long", 32, False),
    "I": ("int", 16, True),
    "U": ("unsigned", 16, False),
    "C": ("signed char", 8, True),
    "UC": ("unsigned char", 8, False),
}

VALUES = [0, 1, 2, 0x7F, 0x80, 0xFF, 0x100, 0x7FFF, 0x8000, 0xFFFF,
          0x10000, 0x12345678, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFE,
          0xFFFFFFFF]

# C literal -> (bits, signed, value)
CONSTS = {
    "0L": (32, True, 0), "1L": (32, True, 1), "-1L": (32, True, -1),
    "5L": (32, True, 5), "-3L": (32, True, -3), "0xFFL": (32, True, 255),
    "0x100L": (32, True, 256), "0xFFFFL": (32, True, 65535),
    "0x10000L": (32, True, 65536), "100000L": (32, True, 100000),
    "0x7FFFFFFFL": (32, True, 0x7FFFFFFF),
    "0x80000000UL": (32, False, 0x80000000),
    "0xFFFFFFFFUL": (32, False, 0xFFFFFFFF),
    "0xEDB88320UL": (32, False, 0xEDB88320),
    "0xFF00FFUL": (32, False, 0xFF00FF),
    "0x20000UL": (32, False, 0x20000),
    "5": (16, True, 5), "-3": (16, True, -3), "0x7FFF": (16, True, 0x7FFF),
    "40000u": (16, False, 40000),
}

OPS = ["+", "-", "&", "|", "^", "==", "!=", "<", ">", "<=", ">="]
CMP = {"==", "!=", "<", ">", "<=", ">="}


def promote(t):
    bits, signed = t
    return (16, True) if bits < 16 else t


def common(ta, tb):
    ta, tb = promote(ta), promote(tb)
    if ta[0] == 32 or tb[0] == 32:
        if (ta[0] == 32 and not ta[1]) or (tb[0] == 32 and not tb[1]):
            return (32, False)
        return (32, True)
    return (16, False) if not (ta[1] and tb[1]) else (16, True)


def model_binop(op, a, ta, b, tb):
    """Value and type of ``a op b``; a, b are Python ints of types ta, tb."""
    ct = common(ta, tb)
    x, y = wrap(a, *ct), wrap(b, *ct)
    if op in CMP:
        res = {"==": x == y, "!=": x != y, "<": x < y, ">": x > y,
               "<=": x <= y, ">=": x >= y}[op]
        return int(res), (16, True)
    res = {"+": x + y, "-": x - y, "&": x & y, "|": x | y, "^": x ^ y}[op]
    return wrap(res, *ct), ct


def to_ulong(v):
    return v & 0xFFFFFFFF


def val_of(kind, raw):
    _, bits, signed = KINDS[kind]
    return wrap(raw, bits, signed)


# One entry per test function: (c_expr, model(i, j) -> (value, type)),
# where the operands are A (from VALUES[i]) and B (from VALUES[j]).
def build_cases():
    cases = []
    big = [k for k in KINDS if KINDS[k][1] == 32]
    allk = list(KINDS)
    for ka in allk:
        for kb in allk:
            if ka not in big and kb not in big:
                continue
            for op in OPS:
                ta = KINDS[ka][1:]
                tb = KINDS[kb][1:]
                cases.append((
                    f"A {op} B", ka, kb,
                    lambda x, y, op=op, ka=ka, kb=kb, ta=ta, tb=tb:
                    model_binop(op, val_of(ka, x), ta, val_of(kb, y), tb)))
    for ka in ["L", "UL", "I", "U"]:
        ta = KINDS[ka][1:]
        for lit, (bits, signed, v) in CONSTS.items():
            for op in OPS:
                cases.append((
                    f"A {op} {lit}", ka, None,
                    lambda x, y, op=op, ka=ka, ta=ta, bits=bits,
                    signed=signed, v=v:
                    model_binop(op, val_of(ka, x), ta, v, (bits, signed))))
                if ka in ("L", "UL") and lit in (
                        "5L", "-3L", "0x10000L", "0x80000000UL", "40000u",
                        "0xFFFFFFFFUL", "100000L", "5"):
                    cases.append((
                        f"{lit} {op} A", ka, None,
                        lambda x, y, op=op, ka=ka, ta=ta, bits=bits,
                        signed=signed, v=v:
                        model_binop(op, v, (bits, signed), val_of(ka, x),
                                    ta)))
    # a nested right or left operand
    for ka in ["L", "UL", "I", "U"]:
        for kb in ["L", "UL", "I", "U"]:
            for op in OPS:
                ta, tb = KINDS[ka][1:], KINDS[kb][1:]

                def inner(y, kb=kb, tb=tb):
                    return model_binop("^", val_of(kb, y), tb, 1,
                                       (32, True))

                def right(x, y, op=op, ka=ka, ta=ta, inner=inner):
                    v, t = inner(y)
                    return model_binop(op, val_of(ka, x), ta, v, t)

                def left(x, y, op=op, kb=kb, tb=tb, ka=ka, ta=ta):
                    v, t = model_binop("^", val_of(ka, x), ta, 1,
                                       (32, True))
                    return model_binop(op, v, t, val_of(kb, y), tb)

                def both(x, y, op=op, ka=ka, kb=kb, ta=ta, tb=tb):
                    v1, t1 = model_binop("+", val_of(ka, x), ta, 1,
                                         (32, True))
                    v2, t2 = model_binop("|", val_of(kb, y), tb, 0x40000,
                                         (32, True))
                    return model_binop(op, v1, t1, v2, t2)

                cases.append((f"(A + 1L) {op} (B | 0x40000L)", ka, kb,
                              both))
                cases.append((f"A {op} (B ^ 1L)", ka, kb, right))
                cases.append((f"(A ^ 1L) {op} B", ka, kb, left))
    # a constant minus a computed value, and shifts by a computed count
    for lit in ("0L", "5L", "-3L", "100000L", "0x80000000UL", "40000u"):
        bits, signed, v = CONSTS[lit]
        for kb in ["L", "UL", "I", "U"]:
            tb = KINDS[kb][1:]
            for op in ("-", "<", ">", "&"):
                def kminus(x, y, op=op, kb=kb, tb=tb, bits=bits,
                           signed=signed, v=v):
                    v2, t2 = model_binop("^", val_of(kb, y), tb, 1,
                                         (32, True))
                    return model_binop(op, v, (bits, signed), v2, t2)
                cases.append((f"{lit} {op} (B ^ 1L)", kb, kb, kminus))
    for ka in ["L", "UL"]:
        ta = KINDS[ka][1:]
        for kb in ["L", "UL", "I", "U", "UC"]:
            for op in ("<<", ">>"):
                for mask in (31, 7):
                    def csh(x, y, op=op, ka=ka, ta=ta, kb=kb, mask=mask):
                        n = val_of(kb, y) & mask
                        v = val_of(ka, x)
                        return wrap(v << n if op == "<<" else v >> n,
                                    *ta), ta
                    cases.append((f"A {op} (B & {mask})", ka, kb, csh))
    # shifts by a constant
    for ka in ["L", "UL"]:
        ta = KINDS[ka][1:]
        for n in list(range(0, 10)) + [11, 12, 15, 16, 17, 20, 23, 24, 25,
                                       28, 31]:
            for op in ("<<", ">>"):
                def sh(x, y, op=op, ka=ka, ta=ta, n=n):
                    v = val_of(ka, x)
                    r = v << n if op == "<<" else v >> n
                    return wrap(r, *ta), ta
                cases.append((f"A {op} {n}", ka, None, sh))
    # statements: A op= B, A op= K and A = A op K, the value of A after
    for ka in ["L", "UL", "I", "U", "C", "UC"]:
        ta = KINDS[ka][1:]
        for op in ["+", "-", "&", "|", "^"]:
            for kb in allk:
                cases.append((
                    f"A {op}= B", ka, kb,
                    lambda x, y, op=op, ka=ka, kb=kb, ta=ta:
                    (wrap(model_binop(op, val_of(ka, x), ta,
                                      val_of(kb, y), KINDS[kb][1:])[0], *ta),
                     ta), True))
            if ka not in ("L", "UL"):
                continue
            for lit, (bits, signed, v) in CONSTS.items():
                for form in ("A {op}= {lit}", "A = A {op} {lit}"):
                    cases.append((
                        form.format(op=op, lit=lit), ka, None,
                        lambda x, y, op=op, ka=ka, ta=ta, bits=bits,
                        signed=signed, v=v:
                        (wrap(model_binop(op, val_of(ka, x), ta, v,
                                          (bits, signed))[0], *ta), ta),
                        True))
            cases.append((f"A = A {op} B", ka, "L",
                          lambda x, y, op=op, ka=ka, ta=ta:
                          (wrap(model_binop(op, val_of(ka, x), ta,
                                            val_of("L", y), (32, True))[0],
                                *ta), ta), True))
    return cases


CASES = [c if len(c) == 5 else (*c, False) for c in build_cases()]
CHUNK = 110


def subst(expr, a, b):
    return re.sub(r"\bB\b", b, re.sub(r"\bA\b", a, expr))


def program(chunk, variant):
    """C source of the test functions of ``chunk``; ``variant`` is
    "global" (A and B are globals) or "param" (the operands are
    parameters, on the IX frame)."""
    lines = ["unsigned long VAL[%d] = {%s};" % (
        len(VALUES), ", ".join(f"{v}UL" for v in VALUES))]
    lines.append("unsigned long acc;")
    lines.append("int k, j;")
    nv = len(VALUES)
    for n, (expr, ka, kb, _m, is_stmt) in enumerate(chunk):
        ca = KINDS[ka][0]
        cb = KINDS[kb][0] if kb else "int"
        if variant == "global":
            body = subst(expr, f"a{n}", f"b{n}")
            lines.append(f"{ca} a{n}; {cb} b{n};")
            lines.append(f"void t{n}(void) {{")
            lines.append(f"  for (k = 0; k < {nv}; k++)"
                         f" for (j = 0; j < {nv}; j++) {{")
            lines.append(f"    a{n} = ({ca})VAL[k]; b{n} = ({cb})VAL[j];")
            if is_stmt:
                lines.append(f"    {body};")
                lines.append(f"    acc = (acc << 3) + (acc >> 29)"
                             f" + (unsigned long)a{n};")
            else:
                lines.append(f"    acc = (acc << 3) + (acc >> 29)"
                             f" + (unsigned long)({body});")
                lines.append(f"    if ({body}) acc ^= 0x5A5A;")
            lines.append("  }")
            lines.append("}")
        else:
            body = subst(expr, "a", "b")
            lines.append(f"unsigned long p{n}({ca} a, {cb} b) {{")
            if is_stmt:
                lines.append(f"  {body};")
                lines.append("  return (unsigned long)a;")
            else:
                lines.append(f"  unsigned long v = (unsigned long)({body});")
                lines.append(f"  if ({body}) v ^= 0x5A5A;")
                lines.append("  return v;")
            lines.append("}")
            lines.append(f"void t{n}(void) {{")
            lines.append(f"  for (k = 0; k < {nv}; k++)"
                         f" for (j = 0; j < {nv}; j++)")
            lines.append(f"    acc = (acc << 3) + (acc >> 29) +"
                         f" p{n}(({ca})VAL[k], ({cb})VAL[j]);")
            lines.append("}")
    lines.append("int main(void) {")
    for n in range(len(chunk)):
        lines.append(f"  acc = 0; t{n}(); printf(\"%lu\\n\", acc);")
    lines.append("  return 0;")
    lines.append("}")
    return "\n".join(lines)


def expected(chunk, variant):
    out = []
    for expr, ka, kb, model, is_stmt in chunk:
        acc = 0
        for x in VALUES:
            for y in VALUES:
                v, t = model(x, y)
                r = to_ulong(wrap(v, *t))
                acc = to_ulong(((acc << 3) & 0xFFFFFFFF) + (acc >> 29))
                if is_stmt:
                    acc = to_ulong(acc + r)
                elif variant == "global":
                    acc = to_ulong(acc + r)
                    if v != 0:
                        acc ^= 0x5A5A
                else:
                    if v != 0:
                        r ^= 0x5A5A
                    acc = to_ulong(acc + r)
        out.append(str(acc))
    return out


CHUNKS = [CASES[i:i + CHUNK] for i in range(0, len(CASES), CHUNK)]


@needs_toolchain
@pytest.mark.parametrize("variant", ["global", "param"])
@pytest.mark.parametrize("idx", range(len(CHUNKS)))
def test_long_expression_matrix(tmp_path, idx, variant):
    chunk = CHUNKS[idx]
    got = build_and_run(tmp_path, program(chunk, variant))
    want = expected(chunk, variant)
    assert len(got) == len(want), got[:5]
    bad = [(chunk[n][0], chunk[n][1], chunk[n][2], got[n], want[n])
           for n in range(len(want)) if got[n] != want[n]]
    assert not bad, bad[:10]


# ---------------------------------------------------------------------
# Bugs the differential tester found next to these paths.
# ---------------------------------------------------------------------

@needs_toolchain
def test_long_switch_compares_all_four_bytes(tmp_path):
    """case 65536 and case -65536 are not 0 for a long (the 16-bit compare
    chain looked at HL alone)."""
    src = """
    long pick(long v) {
        switch (v) {
        case 65536L: return 1;
        case -65536L: return 2;
        case 0L: return 3;
        case 0x12345678L: return 4;
        case 70000L: return 5;
        default: return 9;
        }
    }
    unsigned long upick(unsigned long v) {
        switch (v) {
        case 0x80000000UL: return 1;
        case 0xFFFFFFFFUL: return 2;
        case 0x00010000UL: return 3;
        default: return 0;
        }
    }
    int main(void) {
        long vs[] = {0L, 65536L, -65536L, 70000L, 0x12345678L, 1L, -1L,
                     0x7FFF0000L, 0x00010001L};
        int i;
        unsigned long us[] = {0UL, 0x80000000UL, 0xFFFFFFFFUL, 0x10000UL,
                              0x8000UL};
        for (i = 0; i < 9; i++) printf("%ld\\n", pick(vs[i]));
        for (i = 0; i < 5; i++) printf("%lu\\n", upick(us[i]));
        return 0;
    }
    """
    got = build_and_run(tmp_path, src)
    assert got == ["3", "1", "2", "5", "4", "9", "9", "9", "9",
                   "0", "1", "2", "3", "0"]


@needs_toolchain
def test_unsigned_literal_widens_without_sign(tmp_path):
    """unsigned long x = 65534u is 0x0000FFFE: an unsigned literal is
    zero-extended in an initializer, an assignment, a return."""
    src = """
    unsigned long r(void) { return 65534u; }
    unsigned long g;
    int main(void) {
        unsigned long l = 65534u;
        unsigned u = 0x8000u;
        long m = u;
        g = 0xFFFFu;
        printf("%lu\\n%lu\\n%lu\\n%lu\\n", l, r(), g, (unsigned long)m);
        return 0;
    }
    """
    assert build_and_run(tmp_path, src) == ["65534", "65534", "65535",
                                            "32768"]


@needs_toolchain
def test_unsigned_int_with_long_is_signed_long(tmp_path):
    """unsigned + long is a (signed) long when long is wider (C 6.3.1.8):
    the compare below is signed."""
    src = """
    int a; unsigned b;
    int f(void) { return a < (b ^ 1L); }
    int g(void) { return (b ^ 1L) / 2 < 0; }
    int main(void) {
        a = -1; b = 0xFFFE;
        printf("%d %d\\n", f(), g());
        return 0;
    }
    """
    assert build_and_run(tmp_path, src) == ["1 0"]


def test_long_switch_unit_shape():
    code = gen("long v; int r; int main(void){ switch (v) { case 65536L: "
               "r = 1; break; default: r = 2; } return r; }")
    assert "ld\tA,D" in code and "cp\t1" in code
    assert "call\t__cmp32" not in code
@needs_toolchain
def test_long_index_and_statements_end_to_end(tmp_path):
    src = r"""
    int tab[8] = {10, 11, 12, 13, 14, 15, 16, 17};
    long ltab[4] = {100000L, -2L, 7L, 0x7FFFFFFFL};
    unsigned long ul = 5, big = 0x0001FFFFUL;
    long idx = 3;
    unsigned long uidx = 2;
    int main(void) {
        ul += 7; ul ^= 0x80000000UL; ul &= 0xFF00FFFFUL; ul |= 0x00010000UL;
        big += 1; big -= 0x10000UL; big = big + 5L;
        idx += 4;
        printf("%d %d %ld %lu\n", tab[idx], tab[uidx + 1], ltab[uidx], ul);
        printf("%lu %ld %ld\n", big, ltab[idx - 4], 32L - ltab[2]);
        printf("%lu\n", 1UL << (tab[0] - 3));
        return 0;
    }
    """
    ul = (((5 + 7) ^ 0x80000000) & 0xFF00FFFF) | 0x10000
    big = 0x1FFFF + 1 - 0x10000 + 5
    assert build_and_run(tmp_path, src) == [
        "17 13 7 %d" % ul, "%d 2147483647 25" % big, "128"]


@needs_toolchain
def test_compound_assignment_through_a_call_for_the_index(tmp_path):
    """a[f()] op= rhs: the address stays on the stack while the 32-bit
    operation runs, so nothing may be pushed above it (_SavedAddrValue)."""
    ops = ["+", "-", "&", "|", "^", "*"]
    rhss = [("100000L", 100000), ("g", None), ("(g ^ 1L)", None),
            ("gi", None), ("(long)gi * 3", None), ("0xF0F0F0F0UL", None)]
    lines = ["unsigned long g = 0x00012345UL; int gi = -7;",
             "long la[4]; int calls;",
             "int ix(void) { calls++; return 2; }",
             "int main(void) {"]
    want = []
    gv, giv = 0x12345, -7

    def val(rhs):
        return {"100000L": 100000, "g": gv, "(g ^ 1L)": gv ^ 1,
                "gi": giv, "(long)gi * 3": giv * 3,
                "0xF0F0F0F0UL": 0xF0F0F0F0}[rhs]
    for op in ops:
        for rhs, _ in rhss:
            lines.append(f"  la[2] = 0x7000ABCDL; la[ix()] {op}= {rhs};"
                         f" printf(\"%ld\\n\", la[2]);")
            a, b = 0x7000ABCD, val(rhs)
            r = {"+": a + b, "-": a - b, "&": a & b, "|": a | b,
                 "^": a ^ b, "*": a * b}[op]
            want.append(str(wrap(r, 32, True)))
    for op in ("<<", ">>"):
        for n in (1, 3, 5, 8, 17):
            lines.append(f"  la[2] = -0x12345678L; la[ix()] {op}= (g & 0)"
                         f" + {n}; printf(\"%ld\\n\", la[2]);")
            a = -0x12345678
            want.append(str(wrap(a << n if op == "<<" else a >> n, 32,
                                 True)))
    lines.append("  printf(\"%d\\n\", calls);")
    want.append(str(len(want)))
    lines.append("  return 0; }")
    got = build_and_run(tmp_path, "\n".join(lines))
    assert got == want
