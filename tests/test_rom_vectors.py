"""RST vectors and IM 2 of the ROM target.  See docs/rom_hardware.md.

Each program is built with um80 and ul80, and again with
`--asm-syntax=sdas`, sdasz80 and sdldz80 (when the tools exist).  The
images run in bench/romrun with real interrupts: an IM 2 interrupt carries
the data bus byte, an IM 0 interrupt carries an RST opcode.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import HAVE_SDAS

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bench"))
sys.path.insert(0, str(REPO / "bench" / "rom"))

import sndrom  # noqa: E402
import uc80  # noqa: E402
from romrun import Machine  # noqa: E402

HAVE_UM80 = bool(shutil.which("um80") and shutil.which("ul80"))
LIB = uc80.lib_dir()
INC = str(LIB / "include")

pytestmark = pytest.mark.skipif(
    not (HAVE_UM80 or HAVE_SDAS), reason="needs um80 and ul80, or sdas")

FLOWS = ["um80", "sdas"]


def uc80_cmd(*args):
    return [sys.executable, "-m", "uc80.main", *args]


def build(tmp_path, source, flow, rom_libs=None):
    """Build t.c; return (bin, sym) paths."""
    if flow == "um80" and not HAVE_UM80:
        pytest.skip("needs um80 and ul80")
    if flow == "sdas" and not HAVE_SDAS:
        pytest.skip("needs the SDCC sdas tools")
    (tmp_path / "t.c").write_text(source)
    if flow == "sdas":
        from uc80 import sdas
        steps = [uc80_cmd("--target=rom", "--asm-syntax=sdas", "-I", INC,
                          "t.c", "-o", "t.s"),
                 ["sdasz80", "-g", "-l", "-o", "t.s"]]
        for cmd in steps:
            _run(cmd, tmp_path)
        origin = sdas.code_origin_of((tmp_path / "t.s").read_text()) or 0x68
        _run(["sdldz80", *sndrom.sdas_link_flags(origin=origin), "-i", "-j",
              "t.ihx", "t.rel", "-l", str(rom_libs / "uc80_lc.lib"),
              "-l", str(rom_libs / "uc80_rt.lib")], tmp_path)
        _run(["sdobjcopy", "-I", "ihex", "-O", "binary", "t.ihx", "t.bin"],
             tmp_path)
        sndrom.noi_to_sym(tmp_path / "t.noi", tmp_path / "t.sym")
    else:
        _run(uc80_cmd("--target=rom", "t.c", "-o", "t.mac"), tmp_path)
        _run(["um80", "t.mac", "-o", "t.rel"], tmp_path)
        _run(["ul80", "--bin", "-p", "0", "-d", "F800", "--rom-data",
              "--rom-size", "8000", "--ram-size", "800", "--min-stack", "40",
              "-o", "t.bin", "-S", "t.sym", "t.rel",
              str(LIB / "libc_rom.lib"), str(LIB / "runtime.lib")], tmp_path)
    return tmp_path / "t.bin", tmp_path / "t.sym"


def _run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, " ".join(map(str, cmd)) + "\n" + r.stdout + r.stderr
    return r


def machine(paths):
    m = Machine(banks=None, ram_fill="random", sp="poison", track=True)
    m.load_rom(str(paths[0]))
    m.load_symbols(str(paths[1]))
    return m


@pytest.fixture
def libs(request):
    """uc80_lc.lib and uc80_rt.lib for the sdas flow (built once), or None
    when the sdas tools are missing."""
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


IM2_PROGRAM = """
#include <stdint.h>
volatile uint8_t count_a, count_b, other;

#pragma uc80 im2 3 on_a
void on_a(void) { count_a++; }

#pragma uc80 im2 10 on_b critical
void on_b(void) { count_b += 10; }

int main(void)
{
    __asm__("\\tei");
    while (count_a < 4 || count_b < 60)
        ;
    __asm__("\\tdi");
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_im2_two_handlers_count_their_own_interrupts(flow, tmp_path, libs):
    paths = build(tmp_path, IM2_PROGRAM, flow, libs)
    img = paths[0].read_bytes()
    sy = {n.upper(): a for n, a in _syms(paths[1]).items()}
    # reset: DI, IM 2
    assert img[0:3] == b"\xf3\xed\x5e"
    table = sy["__IM2_TABLE"]
    assert table == 0x100
    # slot 3 and slot 10 hold the handler addresses, the rest the default
    word = lambda i: int.from_bytes(img[table + 2 * i:table + 2 * i + 2],
                                    "little")
    assert word(3) == sy["_ON_A"] and word(10) == sy["_ON_B"]
    assert {word(i) for i in range(128) if i not in (3, 10)} == {
        sy["__IM2_DEFAULT"]}
    m = machine(paths)
    # interleave: slot 3 (bus byte 06h), slot 10 (14h), an unbound slot (40h)
    t = 4000
    for k in range(4):
        m.add_irq_at(t, 0x06)
        m.add_irq_at(t + 2500, 0x14)
        m.add_irq_at(t + 5000, 0x40)
        t += 9000
    m.add_irq_at(t, 0x14)
    m.add_irq_at(t + 2500, 0x14)
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt", res
    assert m.peek8("_count_a") == 4
    assert m.peek8("_count_b") == 60
    assert m.rom_writes == []
    # I holds the high byte of the table
    assert m.cpu.get()["I"] == 0x01


@pytest.mark.parametrize("flow", FLOWS)
def test_im2_unbound_slot_runs_the_default_handler(flow, tmp_path, libs):
    paths = build(tmp_path, IM2_PROGRAM, flow, libs)
    m = machine(paths)
    for k in range(5):
        m.add_irq_at(5000 + 1000 * k, 0x7E)      # slot 63: no handler
    m.add_irq_at(30000, 0x06)
    m.add_irq_at(31000, 0x06)
    m.add_irq_at(32000, 0x06)
    m.add_irq_at(33000, 0x06)
    for k in range(6):
        m.add_irq_at(34000 + 1000 * k, 0x14)
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt"
    assert len(m.accepted) == 15
    assert m.peek8("_count_a") == 4 and m.peek8("_count_b") == 60


@pytest.mark.parametrize("flow", FLOWS)
def test_im2_table_address_is_free_to_choose(flow, tmp_path, libs):
    src = "#pragma uc80 im2table 0x0400\n" + IM2_PROGRAM
    paths = build(tmp_path, src, flow, libs)
    sy = {n.upper(): a for n, a in _syms(paths[1]).items()}
    assert sy["__IM2_TABLE"] == 0x400
    m = machine(paths)
    for k in range(4):
        m.add_irq_at(4000 + 3000 * k, 0x06)
        m.add_irq_at(5000 + 3000 * k, 0x14)
    m.add_irq_at(20000, 0x14)
    m.add_irq_at(21000, 0x14)
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt"
    assert m.cpu.get()["I"] == 0x04
    assert m.peek8("_count_a") == 4 and m.peek8("_count_b") == 60


RST_PROGRAM = """
#include <stdint.h>
volatile uint8_t calls, last;

#pragma uc80 rst 10h on_rst
void on_rst(void) { calls++; }

#pragma uc80 rst 0x18 tick interrupt
void tick(void) { last++; }

int main(void)
{
    __asm__("\\trst 10h");
    __asm__("\\trst 10h");
    __asm__("\\trst 10h");
    __asm__("\\tim 0");
    __asm__("\\tei");
    while (last < 3)
        ;
    __asm__("\\tdi");
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_rst_software_call_and_interrupt_handler(flow, tmp_path, libs):
    paths = build(tmp_path, RST_PROGRAM, flow, libs)
    img = paths[0].read_bytes()
    sy = {n.upper(): a for n, a in _syms(paths[1]).items()}
    assert img[0x10] == 0xC3
    assert int.from_bytes(img[0x11:0x13], "little") == sy["_ON_RST"]
    assert img[0x18] == 0xC3
    assert int.from_bytes(img[0x19:0x1B], "little") == sy["_TICK"]
    assert img[0x08] == 0xC9 and img[0x20] == 0xC9      # still RET
    assert img[0:3] == b"\xf3\xed\x56"                  # IM 1 at reset
    m = machine(paths)
    for k in range(3):
        m.add_irq_at(20000 + 2000 * k, 0xDF)            # RST 18h opcode
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt"
    assert m.peek8("_calls") == 3
    assert m.peek8("_last") == 3
    # the interrupt handler ends with RETI (EI first, five pushes)
    a = sy["_TICK"]
    assert img[a] == 0xFB and img[a + 1:a + 6] == b"\xf5\xc5\xd5\xe5\xfd"


IM1_PROGRAM = """
#include <stdint.h>
volatile uint8_t ticks;
#pragma uc80 interrupt tick
void tick(void) { ticks++; }
int main(void)
{
    __asm__("\\tei");
    while (ticks < 5)
        ;
    __asm__("\\tdi");
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_im1_still_works_without_im2(flow, tmp_path, libs):
    paths = build(tmp_path, IM1_PROGRAM, flow, libs)
    img = paths[0].read_bytes()
    assert img[0:3] == b"\xf3\xed\x56"
    assert "__im2_table" not in {n.lower() for n in _syms(paths[1])}
    m = machine(paths)
    m.add_irq_every(2000, start=2000)
    assert m.run(max_tstates=200_000).reason == "halt"
    assert m.peek8("_ticks") == 5


def test_a_program_with_no_vectors_has_no_im2_or_rst_code(tmp_path):
    """No pragma: the crt0 text has no IM 2, no table and a RET at each RST
    vector, so the image is the same as before this feature."""
    from uc80 import rom
    plain = rom.startup_text(LIB / "crt0_rom.mac", None, None, None)
    again = rom.startup_text(LIB / "crt0_rom.mac", None, None, None,
                             rst={}, im2=None)
    assert plain == again
    text = "\n".join(plain)
    assert "IM\t2" not in text and "__im2" not in text
    assert "\n\tLD\tI,A" not in text
    assert text.count("\tRET\n") >= 6
    for a in (0x08, 0x10, 0x18, 0x20, 0x28, 0x30):
        assert f"__vec_rst{a:02x}:\n\tRET" in text


def test_the_program_code_starts_after_the_table_only_with_im2(tmp_path):
    from uc80 import rom
    t = "\n".join(rom.startup_text(LIB / "crt0_rom.mac", None, None, None,
                                   im2=({}, 0x100)))
    assert "\tIM\t2" in t and "\tLD\tI,A" in t and "LD\tA,01H" in t
    assert "__im2_table:" in t and "ORG\t0200H" in t


def _syms(sym):
    out = {}
    for line in Path(sym).read_text().splitlines():
        a, n = line.split()
        out[n] = int(a, 16)
    return out


# ---- errors ---------------------------------------------------------------

def _compile_error(tmp_path, source, text):
    (tmp_path / "e.c").write_text(source)
    r = subprocess.run(uc80_cmd("--target=rom", "e.c", "-o", "e.mac"),
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 1, r.stderr
    assert "uc80: error:" in r.stderr and text in r.stderr, r.stderr
    assert "Traceback" not in r.stderr


F = "void f(void) { }\nvoid g(void) { }\nint main(void) { return 0; }\n"


@pytest.mark.parametrize("pragma,text", [
    ("rst 00h f", "the RST address must be"),
    ("rst 38h f", "use '#pragma uc80 interrupt NAME'"),
    ("rst 0x40 f", "the RST address must be"),
    ("rst 0Ch f", "the RST address must be"),
    ("rst 10h f\n#pragma uc80 rst 10h g", "RST 10h vector is already set to f"),
    ("rst 10h f interrupt\n#pragma uc80 rst 10h g interrupt",
     "RST 10h vector is already set"),
    ("rst 10h f\n#pragma uc80 vector 10h g", "RST 10h vector is already"),
    ("rst 10h f bogus", "expected '#pragma uc80 rst"),
    ("rst 10h nothere", "'nothere' is named"),
    ("im2 128 f", "the IM 2 slot must be 0 to 127"),
    ("im2 -1 f", "the IM 2 slot must be 0 to 127"),
    ("im2 5 f\n#pragma uc80 im2 5 g", "IM 2 slot 5 is already set to f"),
    ("im2 5 f critical\n#pragma uc80 im2 6 f", "interrupt function of another"),
    ("im2table 0x180", "a multiple of 100h"),
    ("im2table 0x0", "a multiple of 100h"),
    ("im2table 0x200\n#pragma uc80 im2table 0x300", "already at 0200h"),
    ("im2 5 nothere", "'nothere' is named"),
])
def test_vector_errors(pragma, text, tmp_path):
    _compile_error(tmp_path, f"#pragma uc80 {pragma}\n" + F, text)


def test_im2_handler_with_a_parameter_is_an_error(tmp_path):
    _compile_error(tmp_path, "#pragma uc80 im2 1 f\nvoid f(int x) { }\n"
                   "int main(void) { return 0; }\n", "parameter")


def test_im2_handler_called_from_c_is_an_error(tmp_path):
    _compile_error(tmp_path, "#pragma uc80 im2 1 f\nvoid f(void) { }\n"
                   "int main(void) { f(); return 0; }\n", "call")


def test_rst_plain_and_vector_pragma_alias(tmp_path):
    """'#pragma uc80 vector 08h NAME' is the same as 'rst 08h NAME'."""
    paths = build(tmp_path, """
volatile unsigned char n;
#pragma uc80 vector 08h h
void h(void) { n++; }
int main(void)
{
    __asm__("\\trst 08h");
    __asm__("\\trst 08h");
    return 0;
}
""", "um80")
    m = machine(paths)
    m.run(max_tstates=100_000)
    assert m.peek8("_n") == 2
