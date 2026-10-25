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
