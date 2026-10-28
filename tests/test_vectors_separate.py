"""Vectors in separate compilation (--no-whole-program).  See
docs/rom_hardware.md ("Vectors in separately compiled modules").

Each C file compiles alone.  The module that has a handler defines the
label of the vector (__int_handler, __nmi_handler, __rst08_handler ...);
crt0_rom_vec.rel jumps to the labels; libc_rom.lib has a default module for
a label that no module defines.  The images run in bench/romrun with real
interrupts, in the um80 flow and in the sdas flow.
"""

import pytest

from .conftest import HAVE_SDAS
from .rom_flows import FLOWS, build_separate, machine, symbols

pytestmark = pytest.mark.skipif(
    not (HAVE_SDAS or __import__("shutil").which("um80")),
    reason="needs um80 and ul80, or sdas")


@pytest.fixture
def libs(request):
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


MAIN_C = """
extern volatile unsigned char ticks, nmis, rsts;
int main(void)
{
    __asm__("\\trst 10h");
    __asm__("\\trst 10h");
    __asm__("\\tei");
    while (ticks < 5 || nmis < 3)
        ;
    __asm__("\\tdi");
    return 0;
}
"""

HANDLERS_C = """
volatile unsigned char ticks, nmis, rsts;
#pragma uc80 interrupt tick
void tick(void) { ticks++; }
#pragma uc80 nmi on_nmi
void on_nmi(void) { nmis++; }
#pragma uc80 rst 10h on_rst
void on_rst(void) { rsts++; }
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_int_nmi_and_rst_handlers_in_a_second_module_run(
        flow, tmp_path, libs):
    paths, errs, link = build_separate(
        tmp_path, {"main.c": MAIN_C, "h.c": HANDLERS_C}, flow, libs)
    assert link.returncode == 0, link.stdout + link.stderr
    assert "uc80: note" in errs[1] and "crt0_rom_vec.rel" in errs[1]
    assert "warning" not in errs[1]
    img = paths[0].read_bytes()
    sy = symbols(paths)
    # the vectors jump to the handlers
    for addr, name in ((0x38, "_TICK"), (0x66, "_ON_NMI"), (0x10, "_ON_RST")):
        assert img[addr] == 0xC3, hex(addr)
        assert int.from_bytes(img[addr + 1:addr + 3], "little") == sy[name]
    m = machine(paths)
    m.add_irq_every(3000, start=3000)
    m.add_nmi_every(5000, start=4000)
    res = m.run(max_tstates=300_000)
    assert res.reason in ("halt", "budget")
    assert m.peek8("_rsts") == 2
    assert m.peek8("_ticks") >= 5 and m.peek8("_nmis") >= 3
    assert m.rom_writes == []


@pytest.mark.parametrize("flow", FLOWS)
def test_a_vector_that_no_module_binds_gets_the_default(
        flow, tmp_path, libs):
    """Only the INT vector is bound: NMI and RST 08h come from the default
    modules of the library (RETN and RET)."""
    src = """
volatile unsigned char ticks;
#pragma uc80 interrupt tick
void tick(void) { ticks++; }
int main(void)
{
    __asm__("\\trst 08h");
    __asm__("\\tei");
    while (ticks < 4)
        ;
    __asm__("\\tdi");
    return 0;
}
"""
    paths, _errs, link = build_separate(tmp_path, {"t.c": src}, flow, libs)
    assert link.returncode == 0, link.stdout + link.stderr
    img = paths[0].read_bytes()
    nmi = int.from_bytes(img[0x67:0x69], "little")
    assert img[0x66] == 0xC3 and img[nmi:nmi + 2] == b"\xed\x45"   # RETN
    rst = int.from_bytes(img[0x09:0x0B], "little")
    assert img[rst] == 0xC9                                           # RET
    m = machine(paths)
    m.add_irq_every(2000, start=2000)
    m.add_nmi_at(2500)
    res = m.run(max_tstates=200_000)
    assert res.reason == "halt"
    assert m.peek8("_ticks") == 4


@pytest.mark.parametrize("flow", FLOWS)
def test_a_program_with_no_vector_pragma_has_no_reference(
        flow, tmp_path, libs):
    """No pragma, no label and no extra bytes: the module links with
    either start-up file."""
    src = "volatile int out;\nint main(void) { out = 5; return 0; }\n"
    for crt0 in ("plain", "vec"):
        d = tmp_path / crt0
        d.mkdir()
        paths, errs, link = build_separate(d, {"t.c": src}, flow, libs,
                                           crt0=crt0)
        assert link.returncode == 0, link.stdout + link.stderr
        assert errs == [""]
        m = machine(paths)
        assert m.run(max_tstates=100_000).reason == "halt"
        assert m.peek16("_out") == 5


@pytest.mark.parametrize("flow", FLOWS)
def test_the_wrong_start_up_file_is_a_link_error(flow, tmp_path, libs):
    """A module with a vector, linked with crt0_rom.rel (fixed vectors),
    does not link: the ROM would never take the interrupt."""
    paths, _errs, link = build_separate(
        tmp_path, {"main.c": MAIN_C, "h.c": HANDLERS_C}, flow, libs,
        crt0="plain")
    assert link.returncode != 0
    assert "__crt0_rom_vec" in (link.stdout + link.stderr).lower()


@pytest.mark.parametrize("flow", FLOWS)
def test_im2_in_separate_compilation_is_an_error(flow, tmp_path):
    from .rom_flows import need_flow, run, uc80_cmd
    need_flow(flow)
    (tmp_path / "t.c").write_text(
        "#pragma uc80 im2 3 h\nvoid h(void) { }\n"
        "int main(void) { return 0; }\n")
    r = run(uc80_cmd("--target=rom", "--no-whole-program", "t.c", "-o",
                     "t.mac"), tmp_path, ok=False)
    assert r.returncode == 1
    assert "IM 2 in separate compilation" in r.stderr


@pytest.mark.parametrize("flow", FLOWS)
def test_two_modules_bind_two_vectors(flow, tmp_path, libs):
    a = """
volatile unsigned char x;
#pragma uc80 interrupt ha
void ha(void) { x++; }
int main(void)
{
    __asm__("\\tei");
    while (x < 3)
        ;
    __asm__("\\tdi");
    return 0;
}
"""
    b = """
volatile unsigned char y;
#pragma uc80 nmi hb
void hb(void) { y++; }
"""
    paths, _errs, link = build_separate(tmp_path, {"a.c": a, "b.c": b},
                                        flow, libs)
    assert link.returncode == 0, link.stdout + link.stderr
    m = machine(paths)
    m.add_irq_every(3000, start=3000)
    m.add_nmi_at(2000)
    m.run(max_tstates=100_000)
    assert m.peek8("_x") == 3 and m.peek8("_y") == 1
