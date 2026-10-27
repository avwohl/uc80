"""Naked functions: SDCC __naked and [[uc80::naked]] (docs/rom_hardware.md).

A naked function has no frame code, no parameter code and no RET: the body
is the assembly of the programmer.  The tests run the code in cpmemu and in
bench/romrun, and test the error cases without any tool.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .test_rom_target import (_ul80_has_rom_options, compile_rom, link_rom,
                              machine, symbols)
from .test_rom_isr import build, expect_error, image

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "src" / "uc80" / "lib"
CPMEMU = next((p for p in (REPO.parent / "cpmemu" / "src" / "cpmemu",
                           Path.home() / "src" / "cpmemu" / "src" / "cpmemu")
               if p.exists()), None)

HAVE_ROM = bool(shutil.which("um80") and _ul80_has_rom_options())
HAVE_CPM = bool(shutil.which("um80") and shutil.which("ul80") and CPMEMU
                and (LIB / "libc.lib").exists())

# The first parameter is at (SP+2), the second at (SP+4), at entry.
SDCC_ADD = """
int add2(int a, int b) __naked
{
    __asm
        ld hl,#2
        add hl,sp
        ld e,(hl)
        inc hl
        ld d,(hl)
        inc hl
        ld a,(hl)
        inc hl
        ld h,(hl)
        ld l,a
        add hl,de
        ret
    __endasm;
}
"""
ATTR_ADD = """
[[uc80::naked]] int add2(int a, int b)
{
    asm("\\tld hl,2\\n\\tadd hl,sp\\n\\tld e,(hl)\\n\\tinc hl\\n\\tld d,(hl)\\n"
        "\\tinc hl\\n\\tld a,(hl)\\n\\tinc hl\\n\\tld h,(hl)\\n\\tld l,a\\n"
        "\\tadd hl,de\\n\\tret\\n");
    ;
}
"""
FLAGS = [(), ("--no-optimize",), ("-O0",), ("--no-register-cc",),
         ("--no-inlining", "--no-dead-elimination", "--no-asm-dce"),
         ("--no-whole-program",)]


def uc80(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "uc80.main", *args],
                          capture_output=True, text=True, cwd=cwd)


def run_cpm(tmp_path, source, *flags):
    (tmp_path / "p.c").write_text(source)
    r = uc80("p.c", "-o", "p.mac", *flags, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(["um80", "p.mac", "-o", "p.rel"], cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    link = ["p.rel"]
    if "--no-whole-program" in flags:
        link.insert(0, str(LIB / "crt0.rel"))
    r = subprocess.run(["ul80", *link, str(LIB / "libc.lib"),
                        str(LIB / "runtime.lib"), "-o", "p.com"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([str(CPMEMU), "p.com"], cwd=tmp_path,
                       capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, PYTHONHASHSEED="0"))
    return r.stdout


MAIN_PRINT = r"""
#include <stdio.h>
int main(void)
{
    int x = 1000;
    printf("%d %d %d\n", add2(x, 234), add2(-5, 3), add2(7, add2(1, 2)));
    return 0;
}
"""


@pytest.mark.skipif(not HAVE_CPM, reason="needs um80, ul80 and cpmemu")
@pytest.mark.parametrize("flags", FLAGS, ids=lambda f: " ".join(f) or "default")
@pytest.mark.parametrize("src", [SDCC_ADD, ATTR_ADD], ids=["sdcc", "attr"])
def test_naked_helper_called_from_c_in_cpm(tmp_path, src, flags):
    out = run_cpm(tmp_path, src + MAIN_PRINT, *flags)
    assert out.split() == ["1234", "-2", "10"]


@pytest.mark.skipif(not HAVE_ROM, reason="needs um80 and ul80")
@pytest.mark.parametrize("flags", FLAGS[:5], ids=lambda f: " ".join(f) or "default")
@pytest.mark.parametrize("src", [SDCC_ADD, ATTR_ADD], ids=["sdcc", "attr"])
def test_naked_helper_called_from_c_in_a_rom(tmp_path, src, flags):
    build(tmp_path, src + """
volatile int r1, r2;
int main(void)
{
    int x = 1000;
    r1 = add2(x, 234);
    r2 = add2(-5, add2(1, 2));
    return 0;
}
""", *flags)
    m = machine(tmp_path)
    assert m.run(max_tstates=500_000).reason == "halt"
    assert m.peek16("_r1") == 1234
    assert m.peek16("_r2") == 0xFFFE        # -5 + 3


@pytest.mark.skipif(not HAVE_ROM, reason="needs um80 and ul80")
def test_naked_body_is_exactly_the_asm(tmp_path):
    build(tmp_path, SDCC_ADD + """
volatile int r;
int main(void) { r = add2(1, 2); return 0; }
""")
    img = image(tmp_path)
    a = symbols(tmp_path)["_ADD2"]
    # LD HL,2 / ADD HL,SP: the first bytes of the function
    assert img[a:a + 4] == b"\x21\x02\x00\x39"


NAKED_NMI = """
#include <stdint.h>
volatile uint8_t n;
%s
int main(void) { while (n < 3) ; return 0; }
"""


@pytest.mark.skipif(not HAVE_ROM, reason="needs um80 and ul80")
@pytest.mark.parametrize("spell", ["pragma", "sdcc"])
def test_naked_nmi_handler_runs_in_a_rom(tmp_path, spell):
    body = """
    __asm
        push af
        push hl
        ld hl,#_n
        inc (hl)
        pop hl
        pop af
        retn
    __endasm;
"""
    if spell == "pragma":
        src = ("#pragma uc80 nmi on_nmi\n"
               "void on_nmi(void) __naked {" + body + "}\n")
    else:
        src = "void on_nmi(void) __critical __interrupt __naked {" + body + "}\n"
    build(tmp_path, NAKED_NMI % src)
    img = image(tmp_path)
    assert img[0x66] == 0xC3                     # JP on_nmi
    a = symbols(tmp_path)["_ON_NMI"]
    assert int.from_bytes(img[0x67:0x69], "little") == a
    assert img[a] == 0xF5                        # PUSH AF: the asm, nothing before
    m = machine(tmp_path)
    m.add_nmi_every(1000, start=1000, count=3)
    res = m.run(max_tstates=100_000)
    assert res.reason == "halt"
    assert m.peek8("_n") == 3
    assert m.rom_writes == []


@pytest.mark.skipif(not HAVE_ROM, reason="needs um80 and ul80")
def test_naked_int_handler_with_reti_runs_in_a_rom(tmp_path):
    build(tmp_path, """
#include <stdint.h>
volatile uint8_t n;
#pragma uc80 interrupt tick
void tick(void) __naked
{
    __asm
        push af
        push hl
        ld hl,#_n
        inc (hl)
        pop hl
        pop af
        ei
        reti
    __endasm;
}
int main(void)
{
    __asm__("\\tei");
    while (n < 4) ;
    __asm__("\\tdi");
    return 0;
}
""")
    a = symbols(tmp_path)["_TICK"]
    assert image(tmp_path)[a] == 0xF5            # no EI, no pushes from uc80
    m = machine(tmp_path)
    m.add_irq_every(2000, start=2000)
    assert m.run(max_tstates=200_000).reason == "halt"
    assert m.peek8("_n") == 4


@pytest.mark.skipif(not HAVE_ROM, reason="needs um80 and ul80")
def test_naked_referenced_only_from_asm_is_kept(tmp_path):
    """A static naked function that only an asm block calls survives the
    dead-function pass and the assembly DCE; the call runs."""
    build(tmp_path, """
volatile int r;
static void seven(void) __naked
{
    __asm
        ld hl,#7
        ld (_r),hl
        ret
    __endasm;
}
void use(void) { __asm__("\\tcall _seven"); }
int main(void) { use(); return 0; }
""")
    m = machine(tmp_path)
    assert m.run(max_tstates=100_000).reason == "halt"
    assert m.peek16("_r") == 7


# ---- tests that need no tool ----------------------------------------------

def compile_only(tmp_path, source, *flags):
    (tmp_path / "e.c").write_text(source)
    return uc80("e.c", "-o", "e.mac", *flags, cwd=tmp_path)


def test_other_statement_is_an_error_with_the_line(tmp_path):
    r = compile_only(tmp_path, """int f(int a) __naked
{
    __asm
        ret
    __endasm;
    a = 3;
}
int main(void) { return f(1); }
""")
    assert r.returncode == 1
    assert "uc80: error:" in r.stderr and "naked function 'f'" in r.stderr
    assert "6" in r.stderr.split("naked")[0]
    assert "Traceback" not in r.stderr


@pytest.mark.parametrize("body", ["return 1;", "int x;", "{ asm(\"nop\"); }",
                                  "if (1) { asm(\"nop\"); }"])
def test_c_statements_and_declarations_are_errors(tmp_path, body):
    r = compile_only(tmp_path, "int f(void) __naked { " + body + " }\n"
                     "int main(void) { return f(); }\n")
    assert r.returncode == 1, r.stdout
    assert "naked function 'f'" in r.stderr
    assert "Traceback" not in r.stderr


def test_empty_statements_and_several_asm_blocks_are_allowed(tmp_path):
    r = compile_only(tmp_path, """void f(void) __naked
{
    ;
    __asm nop __endasm;
    ;
    asm("\\tret");
}
int main(void) { f(); return 0; }
""")
    assert r.returncode == 0, r.stderr
    code = (tmp_path / "e.mac").read_text()
    body = code.split("_f:")[1].split("; Function main")[0]
    assert "push" not in body.lower() and "ix" not in body.lower()
    assert body.lower().count("ret") == 1


def test_naked_without_asm_warns(tmp_path):
    r = compile_only(tmp_path, "void f(void) __naked { }\n"
                     "int main(void) { f(); return 0; }\n")
    assert r.returncode == 0
    assert "e.c:1: naked function 'f' has no asm" in r.stderr


def test_naked_on_a_variable_is_an_error(tmp_path):
    r = compile_only(tmp_path, "[[uc80::naked]] int x;\n"
                     "int main(void) { return 0; }\n")
    assert r.returncode == 1
    assert "must be on a function" in r.stderr


def test_naked_is_never_inlined(tmp_path):
    r = compile_only(tmp_path, SDCC_ADD + """
int main(void) { return add2(1, 2); }
""")
    assert r.returncode == 0, r.stderr
    code = (tmp_path / "e.mac").read_text()
    assert "call\t_add2" in code


def test_naked_function_is_not_called_with_registers(tmp_path):
    r = compile_only(tmp_path, SDCC_ADD + """
int main(void) { int a = 5; return add2(a, 6); }
""")
    assert r.returncode == 0, r.stderr
    main = (tmp_path / "e.mac").read_text().split("_main:")[1]
    assert "push\tHL" in main                    # arguments on the stack


def test_called_naked_isr_is_still_an_error(tmp_path):
    r = compile_only(tmp_path, """
#pragma uc80 nmi h
void h(void) __naked { asm("\\tretn"); }
int main(void) { h(); return 0; }
""", "--target=rom")
    assert r.returncode == 1
    assert "calls the interrupt function 'h'" in r.stderr


def test_naked_isr_with_parameter_is_an_error(tmp_path):
    r = compile_only(tmp_path, """
#pragma uc80 nmi h
void h(int a) __naked { asm("\\tretn"); }
int main(void) { return 0; }
""", "--target=rom")
    assert r.returncode == 1
    assert "must take no parameters" in r.stderr


NAKED_EXX = """
#include <stdint.h>
volatile uint8_t n;
#pragma uc80 nmi h
void h(void) __naked
{
    __asm
        exx
        push hl
        ld hl,#_n
        inc (hl)
        pop hl
        exx
        retn
    __endasm;
}
int main(void) { while (n < 1) ; return 0; }
"""


def test_naked_handler_gets_no_alt_register_note_or_markers(tmp_path):
    r = compile_only(tmp_path, NAKED_EXX, "--target=rom")
    assert r.returncode == 0, r.stderr
    assert "alternate registers" not in r.stderr
    assert "__UC80_ISR_ALT" not in (tmp_path / "e.mac").read_text()


def test_c_function_called_only_from_naked_asm_is_isr_reachable(tmp_path):
    """The call graph sees '_work' in the naked body: with --isr-safe the
    function keeps its locals on the stack, not in ??AUTO."""
    src = """
#include <stdint.h>
volatile uint8_t n, m;
void work(void)
{
    volatile uint8_t a[4];
    a[0] = 1; a[1] = 2; a[2] = 3; a[3] = 4;
    n = a[0] + a[3];
}
#pragma uc80 nmi h
void h(void) __naked
{
    __asm
        push af
        push bc
        push de
        push hl
        call _work
        pop hl
        pop de
        pop bc
        pop af
        retn
    __endasm;
}
int main(void) { work(); while (n < 9) ; return 0; }
"""
    r = compile_only(tmp_path, src, "--target=rom", "--isr-safe")
    assert r.returncode == 0, r.stderr
    assert "??AUTO" not in (tmp_path / "e.mac").read_text().split(
        "; Function work")[1].split("; Function")[0]
    r = compile_only(tmp_path, src, "--target=rom")
    assert "called from an interrupt function and from" in r.stderr


# ---- attribute spelling, struct and variadic results, mismatches ---------------

ATTR_FORMS = [
    "int add2(int a, int b) __attribute__((naked))",
    "__attribute__((naked)) int add2(int a, int b)",
    "int __attribute__((__naked__, noinline)) add2(int a, int b)",
]
GNU_BODY = ATTR_ADD.split("\n", 1)[1].split("{", 1)[1]


@pytest.mark.skipif(not HAVE_CPM, reason="needs um80, ul80 and cpmemu")
@pytest.mark.parametrize("head", ATTR_FORMS)
def test_gnu_naked_attribute_is_a_naked_function_in_cpm(tmp_path, head):
    out = run_cpm(tmp_path, head + "\n{" + GNU_BODY + MAIN_PRINT)
    assert out.split() == ["1234", "-2", "10"]
    code = (tmp_path / "p.mac").read_text()
    body = code.split("_add2:")[1].split("; Function main")[0]
    assert "push" not in body.lower() and "ix" not in body.lower()


def test_gnu_interrupt_attribute_is_still_an_error(tmp_path):
    r = compile_only(tmp_path, "void f(void) __attribute__((interrupt)) { }\n"
                     "int main(void) { return 0; }\n")
    assert r.returncode == 1
    assert "does not support '__attribute__((interrupt))'" in r.stderr
    r = compile_only(tmp_path, "void f(void) __attribute__((interrupt, naked))"
                     " { }\nint main(void) { return 0; }\n")
    assert r.returncode == 1
    assert "does not support '__attribute__((interrupt))'" in r.stderr


@pytest.mark.parametrize("head", [
    "struct S f(void) __naked",
    "[[uc80::naked]] struct S f(void)",
    "struct S f(void) __attribute__((naked))"])
def test_naked_function_returning_a_big_struct_is_an_error(tmp_path, head):
    r = compile_only(tmp_path, "struct S { int a, b, c; };\n" + head
                     + " { asm(\"\\tret\"); }\n"
                     "int main(void) { f(); return 0; }\n")
    assert r.returncode == 1, r.stdout
    assert "e.c:2: naked function 'f' returns a structure or union" \
        in r.stderr
    assert "hidden result pointer" in r.stderr


def test_naked_function_returning_a_union_is_an_error(tmp_path):
    r = compile_only(tmp_path, "union U { char c[4]; long l; };\n"
                     "union U f(void) __naked { asm(\"\\tret\"); }\n"
                     "int main(void) { f(); return 0; }\n")
    assert r.returncode == 1
    assert "naked function 'f' returns a structure or union" in r.stderr


def test_naked_function_returning_a_small_struct_comes_back_in_hl(tmp_path):
    r = compile_only(tmp_path, "struct S { char a; };\n"
                     "struct S f(void) __naked { asm(\"\\tret\"); }\n"
                     "int main(void) { return 0; }\n")
    assert r.returncode == 0, r.stderr


def test_variadic_naked_function_is_an_error(tmp_path):
    r = compile_only(tmp_path, "int f(int a, ...) __naked\n{\n"
                     "    asm(\"\\tret\");\n}\n"
                     "int main(void) { return f(1); }\n")
    assert r.returncode == 1
    assert "e.c:1: naked function 'f' takes a variable argument list" \
        in r.stderr


def test_naked_prototype_with_a_plain_definition_warns(tmp_path):
    r = compile_only(tmp_path, "void f(void) __naked;\n"
                     "void f(void) { asm(\"\\tret\"); }\n"
                     "int main(void) { f(); return 0; }\n")
    assert r.returncode == 0, r.stderr
    assert ("uc80: warning: e.c:1: the prototype of 'f' is naked, but the "
            "definition at e.c:2 is not") in r.stderr


def test_plain_prototype_with_a_naked_definition_warns(tmp_path):
    r = compile_only(tmp_path, "void f(void);\n"
                     "[[uc80::naked]] void f(void) { asm(\"\\tret\"); }\n"
                     "int main(void) { f(); return 0; }\n")
    assert r.returncode == 0, r.stderr
    assert ("uc80: warning: e.c:1: 'f' is naked in its definition, but this "
            "declaration is not") in r.stderr


def test_matching_naked_prototype_and_definition_do_not_warn(tmp_path):
    r = compile_only(tmp_path, "void f(void) __naked;\n"
                     "void f(void) __naked { asm(\"\\tret\"); }\n"
                     "void g(void);\nvoid g(void) { }\n"
                     "int main(void) { f(); g(); return 0; }\n")
    assert r.returncode == 0 and "warning" not in r.stderr, r.stderr


@pytest.mark.parametrize("src", [
    "typedef int F(int) __naked;\n",
    "typedef int F(int) [[uc80::naked]];\n",
    "typedef int F(int) __attribute__((naked));\n"])
def test_naked_on_a_typedef_is_an_error(tmp_path, src):
    r = compile_only(tmp_path, src + "int main(void) { return 0; }\n")
    assert r.returncode == 1
    assert "e.c:1: " in r.stderr and "on the typedef 'F'" in r.stderr, r.stderr


# ---- an alternate entry label ---------------------------------------------------

TWO_ENTRIES = """
int g(void) __naked
{
    __asm
        ld hl,#1
        ret
_g_alt::
        ld hl,#2
        ret
    __endasm;
}
extern int g_alt(void);
"""


@pytest.mark.skipif(not HAVE_CPM, reason="needs um80, ul80 and cpmemu")
def test_double_colon_label_in_an_asm_block_is_public_in_cpm(tmp_path):
    out = run_cpm(tmp_path, '#include <stdio.h>\n' + TWO_ENTRIES + """
int main(void) { printf("%d %d\\n", g(), g_alt()); return 0; }
""")
    assert out.split() == ["1", "2"]


@pytest.mark.skipif(not HAVE_ROM, reason="needs um80 and ul80")
def test_double_colon_label_in_an_asm_block_is_public_in_a_rom(tmp_path):
    build(tmp_path, TWO_ENTRIES + """
volatile int r1, r2;
int main(void) { r1 = g(); r2 = g_alt(); return 0; }
""")
    m = machine(tmp_path)
    assert m.run(max_tstates=500_000).reason == "halt"
    assert (m.peek16("_r1"), m.peek16("_r2")) == (1, 2)
    assert "_G_ALT" in symbols(tmp_path)


@pytest.mark.skipif(not HAVE_ROM, reason="needs um80 and ul80")
def test_double_colon_label_in_asm_text_is_public_in_a_rom(tmp_path):
    build(tmp_path, """
int g(void) __naked
{
    asm("\\tld hl,1\\n\\tret\\n_g_alt::\\n\\tld hl,2\\n\\tret\\n");
}
extern int g_alt(void);
volatile int r1, r2;
int main(void) { r1 = g(); r2 = g_alt(); return 0; }
""")
    m = machine(tmp_path)
    assert m.run(max_tstates=500_000).reason == "halt"
    assert (m.peek16("_r1"), m.peek16("_r2")) == (1, 2)


# ---- asm as the body of a statement, without braces ----------------------------

@pytest.mark.skipif(not HAVE_ROM, reason="needs um80 and ul80")
def test_asm_statement_as_the_body_of_for_without_braces(tmp_path):
    build(tmp_path, """
volatile int r;
int main(void)
{
    r = 5;
    for (;;) __asm__("\\thalt");
}
""")
    m = machine(tmp_path)
    assert m.run(max_tstates=100_000).reason == "halt"
    assert m.peek16("_r") == 5


@pytest.mark.skipif(not HAVE_CPM, reason="needs um80, ul80 and cpmemu")
def test_asm_statement_as_the_body_of_if_else_while_do_without_braces(
        tmp_path):
    out = run_cpm(tmp_path, """
#include <stdio.h>
volatile int n;
int main(void)
{
    int i;
    for (i = 0; i < 3; i++) asm("\\tld hl,_n\\n\\tinc (hl)\\n");
    if (n == 3) asm volatile("\\tld hl,_n\\n\\tinc (hl)\\n");
    else __asm__("\\tnop");
    if (n == 0) asm("\\tnop"); else __asm__ volatile("\\tld hl,_n\\n\\tinc (hl)\\n");
    i = 2;
    while (i--) __asm__("\\tld hl,_n\\n\\tinc (hl)\\n");
    do __asm__("\\tld hl,_n\\n\\tinc (hl)\\n"); while (0);
    printf("%d\\n", n);
    return 0;
}
""")
    assert out.split() == ["8"]
