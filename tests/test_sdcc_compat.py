"""The SDCC spelling prepass (src/uc80/sdcc_compat.py): what it records,
what it writes, and that it keeps the line numbers.  No assembler needed.
"""

import pytest

from uc80 import sdcc_compat as sc


def run(src):
    found = sc.Found()
    out = sc.prepass(src, "t.c", found)
    return out, found


def test_text_without_sdcc_words_is_returned_as_it_is():
    src = "int x;\nint main(void) { return x; }\n"
    out, found = run(src)
    assert out is src and not found.isrs and not found.sfrs


@pytest.mark.parametrize("spelling,kind", [
    ("__interrupt", "int"),
    ("__interrupt(1)", "int"),
    ("__critical __interrupt", "nmi"),
    ("__interrupt __critical", "nmi"),
    ("__critical __interrupt(3)", "int_crit"),
])
def test_interrupt_spellings_are_recorded_and_erased(spelling, kind):
    src = f"void f(void) {spelling} {{ }}\nint main(void) {{ return 0; }}\n"
    out, found = run(src)
    assert found.isrs == [(kind, "f", "t.c", 1)]
    assert "__interrupt" not in out and "__critical" not in out
    assert out.count("\n") == src.count("\n")
    assert out.startswith("void f(void)")


def test_prototype_with_interrupt_is_erased_but_not_recorded():
    out, found = run("void f(void) __interrupt;\n")
    assert found.isrs == [] and "__interrupt" not in out


def test_sfr_and_banked_sfr():
    out, found = run("__sfr __at(0x08) A;\n__sfr __banked __at(0x0B00) B;\n"
                     "int x;\n")
    assert found.sfrs == {"A": (8, False), "B": (0x0B00, True)}
    assert out.count("\n") == 3 and "__sfr" not in out


def test_at_forms_record_the_name_and_make_the_object_volatile():
    out, found = run("unsigned char a __at(0xF800);\n"
                     "__at(0xF801) unsigned char b;\n"
                     "[[uc80::at(0xF802)]] unsigned char c;\n"
                     "unsigned char d[4] [[uc80::at(0xF810)]];\n")
    assert found.absolute == {"a": 0xF800, "b": 0xF801, "c": 0xF802,
                              "d": 0xF810}
    assert out.count("volatile ") == 4
    assert "__at" not in out and "uc80" not in out
    assert out.count("\n") == 4


def test_critical_block_becomes_two_calls():
    out, _ = run("void f(void) { __critical { x = 1; } y = 2; }\n")
    assert "__builtin_z80_crit_enter" in out
    assert out.index("x = 1") < out.index("__builtin_z80_crit_leave")
    assert out.index("__builtin_z80_crit_leave") < out.index("y = 2")


@pytest.mark.parametrize("src,text", [
    ("void f(void) { __critical { return; } }", "inside '__critical"),
    ("void f(void) { __critical { x = 1; ", "no closing"),
    ("__sfr P;", "expected '__sfr __at(port) NAME;'"),
    ("__sfr __at(0x100) P;", "does not fit 8 bits"),
    ("__sfr __banked __at(0x10000) P;", "does not fit 16 bits"),
    ("int x __at(foo);", "must be an integer constant"),
    ("int x __at(0x10000);", "16-bit address"),
    ("int x __at(0x10) = 3;", "no initial value"),
    ("void f(void) __naked { }", "'__naked' is not supported"),
    ("void f(void) __critical { }", "'__critical' is supported only"),
    ("void f(void) { int x __at(0x10); }", "file-scope"),
])
def test_errors(src, text):
    with pytest.raises(sc.SdccCompatError) as e:
        run(src + "\n")
    assert text in str(e.value)
    assert str(e.value).startswith("t.c:1:")


# ---- __asm ... __endasm (sdas syntax in the block) -------------------------

def asm_lit(out):
    """The text of the asm("...") string of ``out``, with the escapes
    undone."""
    import re
    m = re.search(r'asm\("((?:[^"\\]|\\.)*)"\);', out)
    assert m, out
    return m.group(1).encode().decode("unicode_escape")


def test_asm_block_at_statement_level():
    src = ("void f(void) {\n    __asm\n        ld a,#5\n        ld (_x),a\n"
           "    __endasm;\n}\nint x;\n")
    out, found = run(src)
    assert "__asm" not in out and "__endasm" not in out
    assert out.count("\n") == src.count("\n")
    assert asm_lit(out) == "\tLD\tA,5\n\tLD\t(_x),A\n"
    assert found.asm_blocks == 1


def test_asm_block_at_file_level_and_on_one_line():
    out, _ = run("__asm\n  .db 1,2\n  .dw 0x1234\n__endasm;\n"
                 "void f(void) { __asm nop __endasm; }\n")
    import re
    lits = [m.encode().decode("unicode_escape") for m in
            re.findall(r'asm\("((?:[^"\\]|\\.)*)"\);', out)]
    assert lits[0] == "\tDB\t1,2\n\tDW\t1234H\n"
    assert lits[1] == "\tNOP\n"


@pytest.mark.parametrize("sdas,m80", [
    ("ld a,#0xFF", "LD\tA,0FFH"),
    ("ld hl,#_name+2", "LD\tHL,_name+2"),
    ("ld hl,(_name)", "LD\tHL,(_name)"),
    ("ld a,2(ix)", "LD\tA,(IX+2)"),
    ("ld a,(ix-3)", "LD\tA,(IX-3)"),
    ("ld (iy+1),#7", "LD\t(IY+1),7"),
    ("add a,#1", "ADD\tA,1"),
    ("cp #'A'", "CP\t'A'"),
    ("jp (hl)", "JP\t(HL)"),
    ("out (0x10),a", "OUT\t(10H),A"),
    ("ex af,af'", "EX\tAF,AF'"),
    ("bit 7,a", "BIT\t7,A"),
    ("im 1", "IM\t1"),
    ("ld a,#<_x", "LD\tA,LOW _x"),
    ("ld a,#>_x", "LD\tA,HIGH _x"),
    ("ld bc,#(_a+1)*2", "LD\tBC,0+(_a+1)*2"),
    ("ld a,#_n&3", "LD\tA,_n AND 3"),
    (".ascii \"hi\"", "DB\t'hi'"),
    (".asciz \"a b\"", "DB\t'a b',0"),
    (".ds 4", "DS\t4"),
    (".blkw 2", "DS\t4"),
    ("N = 5", "N\tEQU\t5"),
])
def test_asm_instruction_forms(sdas, m80):
    out, _ = run(f"void f(void) {{ __asm\n {sdas}\n __endasm; }}\n")
    assert asm_lit(out).strip() == m80


def test_asm_local_labels_and_globl():
    out, _ = run("void f(void) { __asm\n"
                 "    .globl _helper, entry\n"
                 "entry:\n"
                 "1$: djnz 1$\n"
                 "    jr 2$\n"
                 "2$: call _helper\n"
                 "__endasm; }\n")
    text = asm_lit(out)
    assert "\tEXTRN\t_helper" in text and "\tPUBLIC\tentry" in text
    assert "@ASM0_1_1:" in text and "\tDJNZ\t@ASM0_1_1" in text
    assert "\tJR\t@ASM0_1_2" in text


def test_asm_local_labels_differ_between_blocks():
    out, found = run("void f(void) { __asm\n1$: jr 1$\n __endasm; }\n"
                     "void g(void) { __asm\n1$: jr 1$\n __endasm; }\n")
    assert "@ASM0_0_1" in out and "@ASM1_0_1" in out and found.asm_blocks == 2


def test_asm_word_in_a_string_or_comment_is_not_a_block():
    src = 'const char *s = "__asm";\n/* __asm */\n// __endasm\nint x;\n'
    out, found = run(src)
    assert out == src and found.asm_blocks == 0


@pytest.mark.parametrize("body,word,line", [
    ("ld a,5", "needs a #", 3),
    (".area _CODE", "not supported", 3),
    (".org 0x100", "not supported", 3),
    (".db \"ab\"", ".ascii", 3),
    ("frob a", "unknown instruction", 3),
    ("ld a,#(1", "missing", 3),
    ("nop\n frob", "unknown instruction", 4),
])
def test_asm_errors_name_the_line(body, word, line):
    src = f"int x;\nvoid f(void) {{ __asm\n {body}\n __endasm; }}\n"
    with pytest.raises(sc.SdccCompatError) as e:
        run(src)
    assert word in str(e.value) and e.value.line == line, str(e.value)


def test_asm_without_endasm_and_endasm_alone():
    with pytest.raises(sc.SdccCompatError) as e:
        run("void f(void) {\n __asm\n nop\n}\n")
    assert "no '__endasm'" in str(e.value) and e.value.line == 2
    with pytest.raises(sc.SdccCompatError) as e:
        run("int x;\n__endasm;\n")
    assert "without '__asm'" in str(e.value) and e.value.line == 2


def test_naked_stays_an_error_next_to_an_asm_block():
    with pytest.raises(sc.SdccCompatError) as e:
        run("void f(void) __naked { __asm\n ret\n __endasm; }\n")
    assert "__naked" in str(e.value)


def test_unbraced_asm_body_gets_braces():
    out, found = run("void g(int i) { for (;;) __asm halt __endasm;\n"
                     " if (i) __asm nop __endasm; else __asm nop __endasm;\n"
                     " while (i) __asm nop __endasm; }\n")
    assert found.asm_blocks == 4
    assert out.count("{ asm(") == 4
    assert out.count("); }") == 4


def test_unbraced_asm_body_compiles():
    import os, subprocess, sys, tempfile
    src = ("void g(int i) { for (;;) __asm halt __endasm;\n"
           " if (i) __asm nop __endasm; else __asm nop __endasm; }\n"
           "int main(void) { g(0); return 0; }\n")
    with tempfile.TemporaryDirectory() as d:
        c, o = os.path.join(d, "a.c"), os.path.join(d, "a.mac")
        open(c, "w").write(src)
        r = subprocess.run([sys.executable, "-m", "uc80.main",
                            "--target=rom", c, "-o", o],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert "HALT" in open(o).read()
