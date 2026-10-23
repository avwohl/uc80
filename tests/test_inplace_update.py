"""In-place updates: ++/--, op= and stores through *p, a[i] and s.m.

An expression statement (and a for-loop init or update) does not use its
value, so the code generator now skips the work that only produces it:

  * x++ and x-- are done as ++x and --x, with no push/pop of the old
    value; a char store does not re-extend the char into HL.
  * ++/-- on a char variable is ``ld HL,x / inc (HL)`` or ``inc (IX+d)``,
    on a 16-bit IX local ``inc (IX+d) / jr NZ / inc (IX+d+1)``, on a long
    in memory a word-wise carry chain (no __add32).  A used value is
    still produced without push/pop where the type allows it.
  * ++/-- on ``*p``, ``a[i]``, ``s.m`` and ``p->m`` computes the address
    once: ``inc (HL)`` for a char; for a 16-bit object the word goes
    through DE.  ++/-- on a long, float or long long element was a 16-bit
    update of the low word; it is now ``x += 1`` (the old value is kept
    for a used postfix value).
  * ``lvalue op= K`` and ``lvalue op= v`` (+ - & | ^, << and >> by a
    constant) on a 16-bit ``*p``, ``a[i]``, ``s.m`` or ``p->m`` compute the
    address once and update the word in HL with the address kept in DE.
  * ``*p = v``, ``a[i] = v`` and ``s.m = v`` with a constant or a simple
    variable v compute the address first, then store with ``ld (HL),K``
    or from DE; with another right side the address is computed without
    touching DE when it can be (``ex DE,HL`` in place of push/pop).
    The address of ``s.m`` for a static struct is ``ld HL,_s+off``, and
    ``a[i]`` of a static array adds the base with ``ld BC,_a``.

The unit tests call generate() directly (no AST optimizer and no
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
                    enable_const_propagation=False,
                    enable_dead_elimination=False)


def ops(code: str, name: str = "f") -> list[str]:
    """Instruction lines of function ``name``, tab-separated, without
    comments, labels, the frame prologue and the final jump to return."""
    start = code.index(f"\n_{name}:")
    end = code.index(f"@{name}_ret:", start)
    out = []
    for line in code[start:end].splitlines():
        if line.startswith("\t") and not line.startswith("\t;"):
            out.append(line.strip().split(";")[0].strip())
    if out[:3] == ["push\tIX", "ld\tIX,0", "add\tIX,SP"]:
        out = out[3:]
    # A frame for locals: ld HL,-n / add HL,SP / ld SP,HL.
    if (len(out) >= 3 and out[0].startswith("ld\tHL,-")
            and out[1:3] == ["add\tHL,SP", "ld\tSP,HL"]):
        out = out[3:]
    while out and out[-1].startswith("jp\t@") and out[-1].endswith("_ret"):
        out.pop()
    return out


GLOBALS = ("int x, y; unsigned u; unsigned char uc; signed char sc; "
           "long l; int *ip; unsigned char *cp; int ia[8]; "
           "unsigned char buf[8]; struct S { unsigned char a; int b; "
           "long c; } s, *ps; ")


def fn(stmts: str, params: str = "void", rtype: str = "void") -> list[str]:
    return ops(gen(GLOBALS + f"{rtype} f({params}) {{ {stmts} }} "
                   "int main(void) { return 0; }"))


def rec(locals_: str, stmts: str) -> list[str]:
    """``stmts`` in a recursive function, so its locals are on the IX
    frame (a non-recursive function keeps them in ??AUTO)."""
    o = ops(gen(GLOBALS + f"void f(int d) {{ {locals_} {stmts} "
                "if (d) f(d - 1); } int main(void) { f(0); return 0; }"))
    return o[:o.index("ld\tL,(IX+4)")]   # up to the `if (d)` test


def no_push(o):
    return not any(i.startswith(("push", "pop")) for i in o)


class TestStatementIncDec:
    def test_char_global(self):
        assert fn("uc++;") == ["ld\tHL,_uc", "inc\t(HL)"]
        assert fn("--sc;") == ["ld\tHL,_sc", "dec\t(HL)"]

    def test_int_global_postfix_is_prefix(self):
        assert fn("x++;") == ["ld\tHL,(_x)", "inc\tHL", "ld\t(_x),HL"]
        assert fn("u--;") == ["ld\tHL,(_u)", "dec\tHL", "ld\t(_u),HL"]

    def test_pointer_step(self):
        assert fn("ip++;") == ["ld\tHL,(_ip)", "inc\tHL", "inc\tHL",
                               "ld\t(_ip),HL"]

    def test_ix_locals(self):
        o = rec("int i = 5; unsigned char c = 1;", "i++; c--;")
        tail = o[o.index("ld\t(IX-3),A") + 1:]
        assert tail[0] == "inc\t(IX-2)"
        assert tail[1].startswith("jr\tNZ,@INC")
        assert tail[2:] == ["inc\t(IX-1)", "dec\t(IX-3)"]

    def test_ix_int_decrement_borrows(self):
        o = rec("int i = 5;", "i--;")
        tail = o[o.index("ld\t(IX-1),H") + 1:]
        assert tail[:3] == ["ld\tA,(IX-2)", "sub\t1", "ld\t(IX-2),A"]
        assert tail[3].startswith("jr\tNC,@INC")
        assert tail[4] == "dec\t(IX-1)"

    def test_for_update_has_no_push(self):
        o = fn("int i; for (i = 0; i < 10; i++) x += i;")
        assert no_push(o)

    def test_shared_char_local(self):
        o = fn("unsigned char c = 3; c++; uc = c;")
        assert "inc\t(HL)" in o and no_push(o)
        assert "ld\tH,0" not in o

    def test_long_global_no_helper(self):
        o = fn("l++;")
        assert o[:5] == ["ld\tHL,(_l)", "inc\tHL", "ld\t(_l),HL",
                         "ld\tA,H", "or\tL"]
        assert o[5].startswith("jr\tNZ,@INC")
        assert o[6:] == ["ld\tHL,(_l+2)", "inc\tHL", "ld\t(_l+2),HL"]
        o = fn("l--;")
        assert o[:5] == ["ld\tHL,(_l)", "ld\tA,H", "or\tL", "dec\tHL",
                         "ld\t(_l),HL"]
        assert not any("__add32" in i for i in fn("--l;"))


class TestUsedIncDec:
    def test_char_global_postfix_value(self):
        assert fn("return uc++;", rtype="int") == [
            "ld\tA,(_uc)", "ld\tL,A", "inc\tA", "ld\t(_uc),A", "ld\tH,0"]

    def test_char_global_prefix_value(self):
        assert fn("return --sc;", rtype="int") == [
            "ld\tA,(_sc)", "dec\tA", "ld\t(_sc),A", "ld\tL,A", "ld\tA,L",
            "rlca", "sbc\tA,A", "ld\tH,A"]

    def test_element_postfix_value(self):
        o = fn("return buf[3]++;", rtype="int")
        assert o == ["ld\tHL,_buf+3", "ld\tA,(HL)", "inc\t(HL)", "ld\tL,A",
                     "ld\tH,0"]

    def test_word_member_postfix_value(self):
        o = fn("return ps->b--;", rtype="int")
        assert o == ["ld\tHL,(_ps)", "inc\tHL", "ld\tE,(HL)", "inc\tHL",
                     "ld\tD,(HL)", "dec\tDE", "ld\t(HL),D", "dec\tHL",
                     "ld\t(HL),E", "ex\tDE,HL", "inc\tHL"]


class TestLvalueIncDec:
    def test_char_element(self):
        assert fn("buf[x]++;") == ["ld\tHL,(_x)", "ld\tBC,_buf",
                                   "add\tHL,BC", "inc\t(HL)"]

    def test_char_member(self):
        assert fn("s.a--;") == ["ld\tHL,_s", "dec\t(HL)"]

    def test_word_deref(self):
        o = fn("(*ip)++;")
        assert o[:2] == ["ld\tHL,(_ip)", "inc\t(HL)"]
        assert o[2].startswith("jr\tNZ,@INC")
        assert o[3:] == ["inc\tHL", "inc\t(HL)"]

    def test_word_element_decrement(self):
        assert fn("ia[2]--;") == ["ld\tHL,_ia+4", "ld\tE,(HL)", "inc\tHL",
                                  "ld\tD,(HL)", "dec\tDE", "ld\t(HL),D",
                                  "dec\tHL", "ld\t(HL),E"]

    def test_long_member_is_32_bit(self):
        o = fn("s.c++;")
        assert (any("__add32" in i for i in o) or "adc\tHL,BC" in o
                or ("add\tHL,BC" in o and "inc\tDE" in o))


class TestCompoundInPlace:
    def test_deref_plus_constant(self):
        assert fn("*ip += 3;") == [
            "ld\tHL,(_ip)", "ld\tE,(HL)", "inc\tHL", "ld\tD,(HL)",
            "ex\tDE,HL", "inc\tHL", "inc\tHL", "inc\tHL", "ex\tDE,HL",
            "ld\t(HL),D", "dec\tHL", "ld\t(HL),E"]

    def test_member_or_constant(self):
        assert fn("s.b |= 0x80;") == [
            "ld\tHL,_s+1", "ld\tE,(HL)", "inc\tHL", "ld\tD,(HL)",
            "ex\tDE,HL", "ld\tA,L", "or\t128", "ld\tL,A", "ex\tDE,HL",
            "ld\t(HL),D", "dec\tHL", "ld\t(HL),E"]

    def test_element_minus_variable(self):
        o = fn("ia[y] -= x;")
        assert no_push(o)
        assert o.count("ld\tHL,(_y)") == 1
        assert "ld\tBC,(_x)" in o and "sbc\tHL,BC" in o

    def test_shift(self):
        o = fn("ps->b >>= 3;")
        assert no_push(o) and o.count("ld\tHL,(_ps)") == 1
        assert o.count("sra\tH") == 3

    def test_pointer_target_scales(self):
        o = fn("int **pp; *pp += 2;")
        assert no_push(o)
        assert o[5:7] == ["ld\tBC,4", "add\tHL,BC"]   # 2 * sizeof(int *)

    def test_value_used(self):
        o = fn("return *ip &= 0xFF;", rtype="int")
        assert o[-1] == "ex\tDE,HL"


class TestStores:
    def test_constant_through_pointer(self):
        assert fn("*ip = 0x1234;") == ["ld\tHL,(_ip)", "ld\t(HL),52",
                                       "inc\tHL", "ld\t(HL),18"]

    def test_variable_to_member(self):
        assert fn("ps->b = x;") == ["ld\tHL,(_ps)", "inc\tHL",
                                    "ld\tDE,(_x)", "ld\t(HL),E", "inc\tHL",
                                    "ld\t(HL),D"]

    def test_char_variable_widened(self):
        assert fn("ia[1] = sc;") == ["ld\tHL,_ia+2", "ld\tA,(_sc)",
                                     "ld\tE,A", "ld\tA,E", "rlca",
                                     "sbc\tA,A", "ld\tD,A", "ld\t(HL),E",
                                     "inc\tHL", "ld\t(HL),D"]

    def test_expression_keeps_de(self):
        o = fn("*ip = x + y;")
        assert no_push(o)
        assert o[-5:] == ["ex\tDE,HL", "ld\tHL,(_ip)", "ld\t(HL),E",
                          "inc\tHL", "ld\t(HL),D"]
        # A used value is returned in HL.
        assert fn("return *ip = x + y;", rtype="int")[-1] == "ex\tDE,HL"

    def test_char_store_value_not_reextended(self):
        assert fn("uc = x;") == ["ld\tA,(_x)", "ld\t(_uc),A"]

    def test_char_local_init(self):
        o = fn("unsigned char c = 7; uc = c;")
        assert "ld\tH,0" not in o and "ld\tL,A" not in o

    def test_shared_char_store_of_expression(self):
        # A char local in ??AUTO stored from HL: no ld L,A / ld H,0 after
        # the store when the value is not used, kept when it is.
        o = fn("unsigned char c; c = x * y; uc = c;")
        assert "ld\tH,0" not in o
        o = fn("unsigned char c; return c = x * y;", rtype="int")
        assert o[-2:] == ["ld\tL,A", "ld\tH,0"]

    def test_long_store_discarded(self):
        o = fn("*(long *)ip = l;")
        assert o.count("push\tHL") == 1

    def test_long_constant(self):
        assert fn("s.c = 0x12345678L;") == [
            "ld\tHL,_s+3", "ld\t(HL),120", "inc\tHL", "ld\t(HL),86",
            "inc\tHL", "ld\t(HL),52", "inc\tHL", "ld\t(HL),18"]

    def test_volatile_word_low_byte_first(self):
        o = fn("volatile int *vp; *vp += 3;")
        assert o[-4:] == ["dec\tHL", "ld\t(HL),E", "inc\tHL", "ld\t(HL),D"]

    def test_assignment_value_used(self):
        assert fn("return *ip = 5;", rtype="int")[-1] == "ld\tHL,5"


# ---------------------------------------------------------------------
# End-to-end: build and run whole programs, compare with a model.
# ---------------------------------------------------------------------

needs_toolchain = pytest.mark.skipif(
    not (CPMEMU.exists() and shutil.which("um80") and shutil.which("ul80")
         and (LIB_DIR / "libc.lib").exists()),
    reason="needs cpmemu, um80, ul80 and the built libraries")


def build_and_run(tmp_path, source, name="prog"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text("#include <stdio.h>\n" + source)
    mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "--printf", "long", "--printf", "float",
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


# name: (C type, bits, signed, initial values)
TYPES = {
    "uc": ("unsigned char", 8, False, [0, 127, 255]),
    "sc": ("signed char", 8, True, [0, -1, -128]),
    "i": ("int", 16, True, [-1, 255, -32768]),
    "u": ("unsigned", 16, False, [0, 255, 65535]),
    "l": ("long", 32, True, [-1, 65535, 0x7FFFFFFF]),
    "ul": ("unsigned long", 32, False, [0, 65535, 0xFFFFFFFF]),
}
# Right-side variables: name, C type, value.
RVARS = [("vi", "int", -3), ("vu", "unsigned", 40000),
         ("vsc", "signed char", -2), ("vuc", "unsigned char", 200)]
KS = [1, 255, -1, 0x1234]
SHIFTS = [1, 4, 8]

# Forms of lvalue: where the case runs (main, a shared-storage function or
# a recursive one with IX locals) and the C text for type name t.
FORMS = {
    "g": ("main", "g_{t}"),
    "p": ("main", "(*p_{t})"),
    "a": ("main", "a_{t}[vi2]"),
    "k": ("main", "a_{t}[3]"),
    "s": ("main", "s_{t}.m"),
    "ps": ("main", "ps_{t}->m"),
    "sh": ("sh", "w_{t}"),
    "ix": ("ix", "w_{t}"),
}


def _decls():
    out = ["long r = 1; int vi2 = 2;"]
    for vn, ct, v in RVARS:
        out.append(f"{ct} {vn} = {v};")
    for t, (ct, bits, s, vals) in TYPES.items():
        out.append(f"{ct} g_{t}; {ct} a_{t}[5]; {ct} *p_{t} = &a_{t}[1]; "
                   f"struct S_{t} {{ char pad; {ct} m; }} s_{t}, *ps_{t};")
    return "\n".join(out)


def _locals():
    return " ".join(f"{ct} w_{t};" for t, (ct, *_r) in TYPES.items())


def _lit(v, bits, signed):
    if bits == 32:
        return f"{v}L" if signed or v < 0x80000000 else f"{v}UL"
    return str(v)


OPS = {"+": lambda a, b: a + b, "-": lambda a, b: a - b,
       "&": lambda a, b: a & b, "|": lambda a, b: a | b,
       "^": lambda a, b: a ^ b}


def _cases():
    """(where, C text, expected output lines) for each case.  ++/-- is
    tested with its value used and as a statement; the other updates
    alternate between the two."""
    cases = []
    for t, (ct, bits, s, vals) in TYPES.items():
        for form, (where, pat) in FORMS.items():
            lv = pat.format(t=t)
            for x0 in vals:
                init = f"{lv} = ({ct}){_lit(x0, bits, s)};"
                x0 = wrap(x0, bits, s)
                n = [0]

                def add(expr, result, new, both=False):
                    show = wrap(result, 32, True)
                    n[0] += 1
                    if both or n[0] % 2:
                        cases.append((where, f"{init} r = (long)({expr}); "
                                      f'printf("%ld %ld\\n", r, (long){lv});',
                                      [f"{show} {wrap(new, 32, True)}"]))
                    if both or not n[0] % 2:
                        cases.append((where, f"{init} {expr}; "
                                      f'printf("%ld\\n", (long){lv});',
                                      [f"{wrap(new, 32, True)}"]))

                up, down = wrap(x0 + 1, bits, s), wrap(x0 - 1, bits, s)
                add(f"{lv}++", x0, up, True)
                add(f"++{lv}", up, up, True)
                add(f"{lv}--", x0, down, True)
                add(f"--{lv}", down, down, True)
                for op, f in OPS.items():
                    for k in KS:
                        v = wrap(f(x0, k), bits, s)
                        add(f"{lv} {op}= {k}", v, v)
                    for vn, vct, vv in RVARS[::3 if op in "&|" else 1]:
                        v = wrap(f(x0, vv), bits, s)
                        add(f"{lv} {op}= {vn}", v, v)
                for k in SHIFTS:
                    if k >= bits:
                        continue
                    v = wrap(x0 << k, bits, s)
                    add(f"{lv} <<= {k}", v, v)
                    v = wrap(x0 >> k, bits, s)
                    add(f"{lv} >>= {k}", v, v)
                for vn, vct, vv in RVARS:
                    v = wrap(vv, bits, s)
                    add(f"{lv} = {vn}", v, v)
                for k in (1, 256, -1, 0x1234):
                    v = wrap(k, bits, s)
                    add(f"{lv} = {k}", v, v)
    return cases


def _program(part):
    body = {"main": [], "sh": [], "ix": []}
    for where, text, _w in part:
        body[where].append(text)
    setup = " ".join(f"ps_{t} = &s_{t};" for t in TYPES)
    return (_decls()
            + f"\nvoid t_sh(void) {{ {_locals()}\n" + "\n".join(body["sh"])
            + " }\nvoid t_ix(int d) { " + _locals() + "\n"
            + "\n".join(body["ix"]) + "\nif (d) t_ix(d - 1); }\n"
            + f"int main(void) {{ {setup}\n" + "\n".join(body["main"])
            + "\nt_sh(); t_ix(0); return 0; }")


@needs_toolchain
@pytest.mark.parametrize("group", range(8))
def test_update_values(tmp_path, group):
    """Every form of lvalue, type, update and right side, against a model."""
    cases = _cases()
    part_all = [c for i, c in enumerate(cases) if i % 8 == group]
    chunk = 110
    for start in range(0, len(part_all), chunk):
        part = part_all[start:start + chunk]
        order = ([c for c in part if c[0] == "main"]
                 + [c for c in part if c[0] == "sh"]
                 + [c for c in part if c[0] == "ix"])
        want = [w for c in order for w in c[2]]
        got = build_and_run(tmp_path, _program(part),
                            f"u{group}_{start // chunk}")
        assert len(got) == len(want), (start, got[-3:])
        bad = [(c[1], w, g) for c, w, g in zip(order, want, got) if w != g]
        assert not bad, bad[:5]


@needs_toolchain
def test_wide_elements(tmp_path):
    """++/-- on long, float and long long elements and members update all
    their bytes; a used postfix value is the old value.

    Every object here is initialized: the embedded runtime's __acc64 and
    __tmp64 are in a second ``common //`` block, which the linker
    overlays on the program's BSS (an older, separate bug)."""
    src = r"""
struct T { char pad; long l; float f; long long q; } t = {0, 65535L, 1.5f,
    4294967295LL};
long la[3] = {65535L, -1L, 0x7FFFFFFFL};
float fa[2] = {2.5f, -1.0f};
long long qa[2] = {-1LL, 0LL};
long *lp = &la[1];
int k = 1;
long r = 1;   /* not in BSS: see the note below */
void run(int d) {
    t.l++; printf("%ld\n", t.l);
    r = t.l--; printf("%ld %ld\n", r, t.l);
    r = ++la[k]; printf("%ld %ld\n", r, la[1]);
    r = (*lp)--; printf("%ld %ld\n", r, *lp);
    la[2]++; printf("%ld\n", la[2]);
    t.f++; ++t.f; printf("%ld\n", (long)(t.f * 10));
    r = (long)(fa[0]-- * 10); printf("%ld %ld\n", r, (long)(fa[0] * 10));
    r = (long)(++fa[k] * 10); printf("%ld %ld\n", r, (long)(fa[1] * 10));
    t.q++; printf("%ld %ld\n", (long)(t.q >> 16), (long)t.q);
    r = (long)(t.q--); printf("%ld %ld\n", r, (long)(t.q >> 16));
    qa[0]++; printf("%ld\n", (long)qa[0]);
    r = (long)(qa[k]--); printf("%ld %ld\n", r, (long)qa[1]);
    if (d) run(d - 1);
}
int main(void) { run(0); return 0; }
"""
    want = ["65536", "65536 65535", "0 0", "0 -1", "-2147483648", "35",
            "25 15", "0 0", "65536 0", "0 65535", "0", "0 -1"]
    assert build_and_run(tmp_path, src, "wide") == want


@needs_toolchain
def test_volatile_and_pointers(tmp_path):
    """A volatile IX int, pointer ++/-- with a step, and a for loop."""
    src = r"""
int arr[6] = {10, 20, 30, 40, 50, 60};
int *gp = &arr[0];
long arr2[3] = {1L, 2L, 3L};
void run(int d) {
    volatile int v = 255; int *p = &arr[1]; long *q = arr2; int i; int s = 0;
    unsigned char c = 250;
    v++; printf("%d\n", v);
    v--; v--; printf("%d\n", v);
    p++; printf("%d\n", *p);
    printf("%d\n", *p--);
    printf("%d\n", *++p);
    q++; printf("%ld\n", *q);
    gp += 2; printf("%d\n", *gp);
    gp++; printf("%d\n", *gp);
    for (i = 0; i < 10; i++) { c++; s += i; }
    printf("%d %d %d\n", i, s, c);
    for (i = 10; i > 0; i--) s -= i;
    printf("%d %d\n", i, s);
    if (d) run(d - 1);
}
int main(void) { run(0); return 0; }
"""
    want = ["256", "254", "30", "30", "30", "2", "30", "40", "10 45 4",
            "0 -10"]
    assert build_and_run(tmp_path, src, "vol") == want
